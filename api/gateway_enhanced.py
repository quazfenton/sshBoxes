#!/usr/bin/env python3
"""
Enhanced SSH Gateway for sshBox using FastAPI
Implements comprehensive security, metrics integration, and proper error handling
"""
import os
import time
import json
import subprocess
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
import threading

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
import uvicorn

# Rate limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Local imports
from api.config import get_settings, Settings
from api.security import (
    validate_token,
    create_token,
    TokenValidationError,
    SSHKeyValidator,
    InputValidator,
    constant_time_compare
)
from api.metrics import MetricsCollector
from api.connection_pool import get_db_connection

# Configure logging
logs_dir = get_settings().storage.logs_dir
os.makedirs(logs_dir, exist_ok=True)

logger = logging.getLogger("gateway")
logger.setLevel(getattr(logging, get_settings().storage.log_level))

# Create formatter
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Add request ID filter
class RequestIDFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, 'request_id'):
            record.request_id = getattr(record, 'request_id', 'N/A')
        return True

logger.addFilter(RequestIDFilter())

# File handler with rotation
log_file = os.path.join(logs_dir, "gateway.log")
file_handler = logging.handlers.RotatingFileHandler(
    log_file,
    maxBytes=get_settings().storage.log_max_bytes,
    backupCount=get_settings().storage.log_backup_count
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Initialize settings and metrics
settings = get_settings()
metrics = MetricsCollector(settings.storage.metrics_file)

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)

# Initialize SSH key validator
ssh_validator = SSHKeyValidator()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler"""
    # Startup
    logger.info("Starting sshBox Gateway")
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"Database type: {settings.database.db_type}")
    logger.info(f"Provisioner type: {settings.provisioner.provisioner_type}")
    
    # Validate configuration
    errors = settings.validate_all()
    if errors:
        for error in errors:
            logger.error(f"Configuration error: {error}")
        if settings.environment == "production":
            raise RuntimeError(f"Configuration validation failed: {', '.join(errors)}")
    
    yield
    
    # Shutdown
    logger.info("Shutting down sshBox Gateway")


app = FastAPI(
    title="sshBox Gateway",
    description="Gateway for ephemeral SSH boxes with enhanced security",
    version=settings.app_version,
    lifespan=lifespan
)

# Add rate limiter to app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware
if settings.allowed_origins:
    origins = [origin.strip() for origin in settings.allowed_origins.split(',')]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )


# Request ID middleware
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    import uuid
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id
    
    # Add to logging context
    extra = {"request_id": request_id}
    
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time"] = str(process_time)
    
    logger.info(
        f"{request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s",
        extra=extra
    )
    
    # Record metrics
    metrics.record_request(
        endpoint=request.url.path,
        success=response.status_code < 500,
        status_code=response.status_code,
        process_time=process_time
    )
    
    return response


# Request/Response models
class TokenRequest(BaseModel):
    token: str = Field(..., description="Invite token")
    pubkey: str = Field(..., description="SSH public key")
    profile: str = Field(default="dev", description="Box profile")
    ttl: int = Field(default=1800, description="Time-to-live in seconds")
    
    @validator('pubkey')
    def validate_pubkey(cls, v):
        is_valid, error = ssh_validator.validate(v)
        if not is_valid:
            raise ValueError(error)
        return v
    
    @validator('ttl')
    def validate_ttl(cls, v):
        if v < settings.provisioner.min_ttl:
            raise ValueError(f"TTL must be at least {settings.provisioner.min_ttl} seconds")
        if v > settings.provisioner.max_ttl:
            raise ValueError(f"TTL must be at most {settings.provisioner.max_ttl} seconds")
        return v


class DestroyRequest(BaseModel):
    session_id: str = Field(..., description="Session ID to destroy")
    
    @validator('session_id')
    def validate_session_id(cls, v):
        is_valid, error = InputValidator.validate_session_id(v)
        if not is_valid:
            raise ValueError(error)
        return v


class ConnectionInfo(BaseModel):
    host: str
    port: int
    user: str
    session_id: str
    profile: Optional[str] = None
    expires_at: Optional[str] = None


class SessionInfo(BaseModel):
    session_id: str
    container_name: str
    ssh_host: Optional[str]
    ssh_port: Optional[int]
    ssh_user: Optional[str]
    profile: str
    ttl: int
    status: str
    created_at: str
    time_left: Optional[int] = None


# Helper functions
def schedule_destroy(
    container_name: str,
    session_id: str,
    ttl: int,
    background_tasks: BackgroundTasks
):
    """Schedule container destruction after TTL"""
    
    def destroy_task():
        request_id = f"destroy-{session_id}"
        logger.info(
            f"Scheduled destruction for session {session_id} in {ttl} seconds",
            extra={"request_id": request_id}
        )
        
        time.sleep(ttl)
        
        try:
            logger.info(
                f"Executing destruction for session {session_id}, container {container_name}",
                extra={"request_id": request_id}
            )
            
            result = subprocess.run(
                [settings.provisioner.provisioner_path.replace('provision', 'destroy'), container_name],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                logger.error(
                    f"Destroy script failed for container {container_name}: {result.stderr}",
                    extra={"request_id": request_id}
                )
                metrics.record_error("destroy_script_failed")
            else:
                logger.info(
                    f"Successfully destroyed container {container_name}",
                    extra={"request_id": request_id}
                )
                metrics.record_session_destruction()
            
            # Update session status in DB
            update_session_status(session_id, 'destroyed')
            
        except subprocess.TimeoutExpired:
            logger.error(
                f"Destroy script timed out for container {container_name}",
                extra={"request_id": request_id}
            )
            metrics.record_error("destroy_timeout")
        except Exception as e:
            logger.error(
                f"Error destroying container {container_name}: {e}",
                extra={"request_id": request_id}
            )
            metrics.record_error("destroy_error")
    
    background_tasks.add_task(destroy_task)


def update_session_status(session_id: str, status: str):
    """Update session status in database"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE sessions SET status = ?, ended_at = ? WHERE session_id = ?",
                (status, datetime.utcnow().isoformat(), session_id)
            )
            conn.commit()
            cur.close()
            logger.info(f"Session {session_id} marked as {status} in database")
    except Exception as e:
        logger.error(f"Error updating session status for {session_id}: {e}")
        metrics.record_error("db_update_failed")


def get_db_connection():
    """Get database connection based on settings"""
    if settings.database.db_type == "postgresql":
        import psycopg2
        return psycopg2.connect(
            host=settings.database.db_host,
            port=settings.database.db_port,
            database=settings.database.db_name,
            user=settings.database.db_user,
            password=settings.database.db_pass
        )
    else:  # Default to SQLite with connection pooling
        return get_db_connection()


# API Endpoints
@app.post("/request", summary="Request a new SSH box", response_model=ConnectionInfo)
@limiter.limit(settings.security.rate_limit_request)
async def handle_request(
    request: Request,
    token_request: TokenRequest,
    background_tasks: BackgroundTasks
):
    """
    Request a new ephemeral SSH box.
    
    - **token**: Valid invite token
    - **pubkey**: SSH public key for authentication
    - **profile**: Box profile (dev, debug, secure-shell, privileged)
    - **ttl**: Time-to-live in seconds
    """
    request_id = getattr(request.state, 'request_id', 'unknown')
    start_time = time.time()
    
    try:
        logger.info(f"Received request for new SSH box, profile: {token_request.profile}")
        
        # Validate token
        try:
            token_payload = validate_token(token_request.token)
        except TokenValidationError as e:
            logger.warning(f"Token validation failed: {e.error_code}")
            metrics.record_error(f"token_validation_{e.error_code.lower()}")
            raise HTTPException(status_code=403, detail=f"Invalid token: {e.error_code}")
        
        # Use TTL from token (more secure than from request)
        ttl = token_payload.ttl
        profile = token_payload.profile
        
        # Generate session ID
        session_id = f"box_{int(time.time() * 1000)}_{os.urandom(4).hex()}"
        container_name = f"sshbox_{session_id}"
        
        # Validate session ID
        is_valid, error = InputValidator.validate_session_id(session_id.replace("box_", ""))
        if not is_valid:
            raise HTTPException(status_code=500, detail=f"Generated invalid session ID: {error}")
        
        logger.info(
            f"Creating session {session_id} with profile {profile}, TTL {ttl}s",
            extra={"request_id": request_id}
        )
        
        # Call provisioner script
        try:
            result = subprocess.run(
                [
                    settings.provisioner.provisioner_path,
                    session_id,
                    token_request.pubkey,
                    profile,
                    str(ttl)
                ],
                capture_output=True,
                text=True,
                timeout=60
            )
        except subprocess.TimeoutExpired:
            logger.error(f"Provisioning timed out for session {session_id}")
            metrics.record_error("provision_timeout")
            raise HTTPException(status_code=504, detail="Provisioning timed out")
        
        if result.returncode != 0:
            logger.error(f"Provisioning failed for session {session_id}: {result.stderr}")
            metrics.record_error("provision_script_failed")
            raise HTTPException(status_code=500, detail=f"Provisioning failed: {result.stderr}")
        
        # Parse provisioner output
        try:
            connection_info = json.loads(result.stdout.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from provisioner: {result.stdout}")
            metrics.record_error("provision_invalid_json")
            raise HTTPException(status_code=500, detail="Invalid response from provisioner")
        
        # Validate connection info
        if not all(k in connection_info for k in ['host', 'port', 'user']):
            logger.error(f"Incomplete connection info: {connection_info}")
            metrics.record_error("provision_incomplete_info")
            raise HTTPException(status_code=500, detail="Incomplete connection info")
        
        # Add session ID to connection info
        connection_info['session_id'] = session_id
        connection_info['profile'] = profile
        connection_info['expires_at'] = (
            datetime.utcnow() + timedelta(seconds=ttl)
        ).isoformat()
        
        # Store session metadata in DB
        try:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO sessions
                    (session_id, container_name, ssh_host, ssh_port, ssh_user, profile, ttl, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        container_name,
                        connection_info.get('host'),
                        connection_info.get('port'),
                        connection_info.get('user'),
                        profile,
                        ttl,
                        'active',
                        datetime.utcnow().isoformat()
                    )
                )
                conn.commit()
                cur.close()
        except Exception as e:
            logger.error(f"Error storing session metadata: {e}")
            metrics.record_error("db_insert_failed")
            # Continue anyway - session exists even if metadata storage failed
        
        logger.info(
            f"Session {session_id} created successfully",
            extra={"request_id": request_id}
        )
        
        # Record metrics
        metrics.record_session_creation(profile)
        metrics.record_timing("provision_time", time.time() - start_time)
        
        # Schedule destruction
        schedule_destroy(container_name, session_id, ttl, background_tasks)
        
        return ConnectionInfo(**connection_info)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in handle_request: {e}", exc_info=True)
        metrics.record_error("unexpected_error")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sessions", summary="List active sessions", response_model=List[SessionInfo])
@limiter.limit(settings.security.rate_limit_sessions)
async def list_sessions(request: Request, status: Optional[str] = None):
    """
    List sessions with optional status filter.
    
    - **status**: Filter by status (active, destroyed, ended)
    """
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            
            if status:
                cur.execute(
                    "SELECT * FROM sessions WHERE status = ? ORDER BY created_at DESC",
                    (status,)
                )
            else:
                cur.execute("SELECT * FROM sessions ORDER BY created_at DESC")
            
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            
            sessions = []
            for row in rows:
                session = dict(zip(columns, row))
                
                # Calculate time left for active sessions
                if session['status'] == 'active' and session['ttl'] and session['created_at']:
                    created_at = session['created_at']
                    if isinstance(created_at, str):
                        created_at = datetime.fromisoformat(created_at.replace('Z', '').split('.')[0])
                    expires_at = created_at + timedelta(seconds=session['ttl'])
                    time_left = max(0, int((expires_at - datetime.utcnow()).total_seconds()))
                    session['time_left'] = time_left
                
                sessions.append(SessionInfo(**session))
            
            cur.close()
        
        return sessions
        
    except Exception as e:
        logger.error(f"Error listing sessions: {e}", exc_info=True)
        metrics.record_error("list_sessions_error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/destroy", summary="Destroy a specific session")
@limiter.limit(settings.security.rate_limit_destroy)
async def destroy_session(request: Request, destroy_request: DestroyRequest):
    """
    Destroy a specific session.
    
    - **session_id**: Session ID to destroy
    """
    try:
        # Get container name from DB
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT container_name, status FROM sessions WHERE session_id = ?",
                (destroy_request.session_id,)
            )
            result = cur.fetchone()
            
            if not result:
                raise HTTPException(status_code=404, detail="Session not found")
            
            container_name, status = result
            
            if status == 'destroyed':
                return {"message": "Session already destroyed"}
        
        # Call destroy script
        destroy_script = settings.provisioner.provisioner_path.replace('provision', 'destroy')
        result = subprocess.run(
            [destroy_script, container_name],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Destroy failed: {result.stderr}")
        
        # Update session status in DB
        update_session_status(destroy_request.session_id, 'destroyed')
        
        metrics.record_session_destruction()
        
        return {"message": f"Session {destroy_request.session_id} destroyed successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error destroying session: {e}", exc_info=True)
        metrics.record_error("destroy_error")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health", summary="Health check endpoint")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": settings.app_version,
        "environment": settings.environment
    }


@app.get("/metrics", summary="Prometheus metrics endpoint")
async def get_metrics():
    """Get metrics in Prometheus format"""
    return metrics.get_prometheus_metrics()


@app.get("/", summary="Root endpoint")
async def root():
    """Root endpoint with API information"""
    return {
        "message": "Welcome to sshBox Gateway",
        "version": settings.app_version,
        "endpoints": {
            "POST /request": "Request a new SSH box",
            "GET /sessions": "List active sessions",
            "POST /destroy": "Destroy a session",
            "GET /health": "Health check",
            "GET /metrics": "Prometheus metrics"
        },
        "documentation": "/docs"
    }


if __name__ == '__main__':
    uvicorn.run(
        app,
        host=settings.gateway_host,
        port=settings.gateway_port,
        log_level=settings.storage.log_level.lower()
    )
