#!/usr/bin/env python3
"""
Enhanced SSH Gateway for sshBox using FastAPI
Includes security fixes, metrics integration, circuit breakers, and structured logging
"""
import os
import hmac
import hashlib
import time
import json
import subprocess
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
import redis
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator
import sqlite3
import logging

# Import sshBox modules
from api.logging_config import setup_logging, add_log_context, log_execution_time
from api.exceptions import (
    SSHBoxError,
    TokenValidationError,
    TokenExpiredError,
    ProvisioningError,
    SessionNotFoundError,
    SessionAlreadyDestroyedError,
    ConfigurationError,
    DatabaseError,
    InvalidInputError,
    SSHKeyError,
    CircuitBreakerOpenError,
    QuotaExceededError,
)
from api.circuit_breaker import database_breaker, provisioning_breaker, CircuitBreakerRegistry
from api.metrics import (
    record_request,
    record_session_creation,
    record_session_destruction,
    record_error,
    record_timing,
    metrics,
)
from api.connection_pool import get_db_connection

# Initialize logging
logger = setup_logging("gateway", log_level=logging.INFO)

# Initialize FastAPI app
app = FastAPI(
    title="sshBox Gateway",
    description="Gateway for ephemeral SSH boxes with enhanced security and monitoring",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get('ALLOWED_ORIGINS', '').split(',') or [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

# Rate limiting (import from slowapi if available)
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded

    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter

    def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={
                "error": "RATE_LIMIT_EXCEEDED",
                "message": str(exc.detail),
                "retry_after": getattr(exc, 'retry_after', None)
            }
        )

    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
    RATE_LIMITING_ENABLED = True
    
    # WARNING: Alert operator if rate limiting disabled in production
    environment = os.environ.get('SSHBOX_ENVIRONMENT', 'development')
    if environment == 'production' and not RATE_LIMITING_ENABLED:
        logger.warning(
            "⚠️  CRITICAL: RATE LIMITING DISABLED IN PRODUCTION! "
            "Install slowapi: pip install slowapi"
        )
        
except ImportError:
    logger.warning("slowapi not installed, rate limiting disabled")
    RATE_LIMITING_ENABLED = False
    limiter = None
    
    # WARNING: Alert operator if rate limiting disabled in production
    environment = os.environ.get('SSHBOX_ENVIRONMENT', 'development')
    if environment == 'production':
        logger.warning(
            "⚠️  CRITICAL: RATE LIMITING DISABLED IN PRODUCTION! "
            "Install slowapi: pip install slowapi"
        )


# ============================================================================
# Configuration
# ============================================================================

def get_gateway_secret() -> str:
    """Get and validate gateway secret"""
    secret = os.environ.get('GATEWAY_SECRET', '')
    
    if not secret:
        raise ConfigurationError(
            "GATEWAY_SECRET environment variable must be set",
            field="GATEWAY_SECRET"
        )
    
    if len(secret) < 32:
        raise ConfigurationError(
            f"GATEWAY_SECRET must be at least 32 characters (got {len(secret)})",
            field="GATEWAY_SECRET"
        )
    
    # Check entropy
    has_upper = any(c.isupper() for c in secret)
    has_lower = any(c.islower() for c in secret)
    has_digit = any(c.isdigit() for c in secret)
    has_special = any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?' for c in secret)
    
    if sum([has_upper, has_lower, has_digit, has_special]) < 3:
        raise ConfigurationError(
            "GATEWAY_SECRET must contain at least 3 of: uppercase, lowercase, digits, special characters",
            field="GATEWAY_SECRET"
        )
    
    return secret


# Load configuration
_gateway_secret: Optional[str] = None


def get_gateway_secret_lazy() -> str:
    global _gateway_secret
    if _gateway_secret is None:
        _gateway_secret = get_gateway_secret()
    return _gateway_secret

PROVISIONER_PATH = os.environ.get('PROVISIONER_PATH', './scripts/box-provision.sh')
DB_TYPE = os.environ.get('DB_TYPE', 'sqlite')
SQLITE_PATH = os.environ.get('SQLITE_PATH', '/tmp/sshbox_sessions.db')
ALLOWED_PROFILES = ["dev", "debug", "secure-shell", "privileged"]
TOKEN_MAX_AGE_SECONDS = int(os.environ.get('TOKEN_MAX_AGE', 300))  # 5 minutes

# Redis connection (optional)
REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
try:
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        decode_responses=True,
        socket_connect_timeout=5
    )
    redis_client.ping()
    REDIS_AVAILABLE = True
    logger.info(f"Redis connected: {REDIS_HOST}:{REDIS_PORT}")
except Exception as e:
    logger.warning(f"Redis unavailable: {e}. Some features disabled.")
    redis_client = None
    REDIS_AVAILABLE = False


# ============================================================================
# Request/Response Models
# ============================================================================

class TokenRequest(BaseModel):
    """Request model for creating a new session"""
    token: str = Field(..., min_length=10, description="HMAC authentication token")
    pubkey: str = Field(..., min_length=50, description="SSH public key")
    profile: str = Field(default="dev", description="Session profile")
    ttl: int = Field(default=1800, ge=60, le=7200, description="Time to live in seconds")
    
    @validator('pubkey')
    def validate_pubkey(cls, v):
        """Validate SSH public key format"""
        v = v.strip()
        valid_prefixes = (
            'ssh-rsa ', 'ssh-ed25519 ', 
            'ecdsa-sha2-nistp256 ', 'ecdsa-sha2-nistp384 ', 'ecdsa-sha2-nistp521 ',
            'sk-ecdsa-sha2-nistp256@openssh.com ', 'sk-ssh-ed25519@openssh.com '
        )
        if not any(v.startswith(prefix) for prefix in valid_prefixes):
            raise ValueError("Invalid SSH public key format. Must start with ssh-rsa, ssh-ed25519, or ecdsa-*")
        
        # Check key length (basic check)
        parts = v.split()
        if len(parts) < 2:
            raise ValueError("SSH public key must have key type and key data")
        
        if len(parts[1]) < 50:
            raise ValueError("SSH public key data too short")
        
        return v
    
    @validator('profile')
    def validate_profile(cls, v):
        """Validate profile is allowed"""
        if v not in ALLOWED_PROFILES:
            raise ValueError(f"Profile must be one of: {', '.join(ALLOWED_PROFILES)}")
        return v


class DestroyRequest(BaseModel):
    """Request model for destroying a session"""
    session_id: str = Field(..., min_length=1, description="Session ID to destroy")


class ConnectionResponse(BaseModel):
    """Response model for successful session creation"""
    host: str
    port: int
    user: str
    session_id: str
    profile: Optional[str] = None
    ttl: Optional[int] = None


class SessionInfo(BaseModel):
    """Session information model"""
    session_id: str
    container_name: str
    ssh_host: str
    ssh_port: int
    ssh_user: str
    profile: str
    ttl: int
    status: str
    created_at: str
    time_left: Optional[int] = None


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    timestamp: str
    version: str = "2.0.0"
    database: str = "connected"
    redis: str = "connected"
    circuit_breakers: Optional[Dict[str, str]] = None


# ============================================================================
# Security Functions
# ============================================================================

def constant_time_in(value: str, allowed_list: List[str]) -> bool:
    """
    Constant-time membership check to prevent timing attacks
    
    Args:
        value: Value to check
        allowed_list: List of allowed values
    
    Returns:
        True if value is in allowed_list, False otherwise
    """
    if not value:
        return False
    
    result = 0
    for item in allowed_list:
        result |= hmac.compare_digest(value.encode(), item.encode())
    return bool(result)


def validate_token(token: str) -> tuple[bool, Optional[str]]:
    """
    Validate HMAC token with enhanced security checks
    
    Args:
        token: Token string in format profile:ttl:timestamp:recipient_hash:notes_hash:signature
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        parts = token.split(':')
        if len(parts) != 6:
            logger.warning(f"Invalid token format: expected 6 parts, got {len(parts)}")
            return False, "Invalid token format"

        profile, ttl_str, timestamp_str, recipient_hash, notes_hash, signature = parts

        # Validate TTL is numeric
        try:
            ttl = int(ttl_str)
            if ttl <= 0 or ttl > 7200:
                logger.warning(f"Invalid TTL in token: {ttl}")
                return False, "Invalid TTL value"
        except ValueError:
            logger.warning(f"Invalid TTL format in token: {ttl_str}")
            return False, "Invalid TTL format"

        # Validate timestamp format
        try:
            token_timestamp = int(timestamp_str)
        except ValueError:
            logger.warning(f"Invalid timestamp format in token: {timestamp_str}")
            return False, "Invalid timestamp format"
        
        # Check token age (prevent replay attacks)
        current_time = int(time.time())
        token_age = current_time - token_timestamp
        
        if token_age < 0:
            logger.warning(f"Token timestamp is in the future: {token_timestamp}")
            return False, "Invalid token timestamp"
        
        if token_age > TOKEN_MAX_AGE_SECONDS:
            logger.warning(f"Token expired: age={token_age}s, max={TOKEN_MAX_AGE_SECONDS}s")
            return False, "Token expired"
        
        # Validate profile using constant-time comparison
        if not constant_time_in(profile, ALLOWED_PROFILES):
            logger.warning(f"Invalid profile in token: {profile}")
            return False, "Invalid profile"
        
        # Validate signature using constant-time comparison
        expected_payload = f"{profile}:{ttl_str}:{timestamp_str}:{recipient_hash}:{notes_hash}"
        expected_signature = hmac.new(
            get_gateway_secret_lazy().encode(),
            expected_payload.encode(),
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(signature, expected_signature):
            logger.warning("Invalid token signature")
            return False, "Invalid token signature"

        return True, None
        
    except Exception as e:
        logger.error(f"Error validating token: {e}")
        return False, "Token validation error"


def validate_session_id(session_id: str) -> bool:
    """
    Validate session ID format to prevent injection attacks
    
    Args:
        session_id: Session ID to validate
    
    Returns:
        True if valid, raises InvalidInputError otherwise
    """
    import re
    if not re.match(r'^[a-zA-Z0-9_-]+$', session_id):
        raise InvalidInputError(
            field="session_id",
            reason="Must contain only alphanumeric characters, dashes, and underscores",
            value=session_id
        )
    return True


# ============================================================================
# Database Operations
# ============================================================================

@contextmanager
def get_db_cursor():
    """Context manager for database cursor with error handling"""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        yield cursor
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Database error: {e}")
        raise DatabaseError(
            reason=str(e),
            operation="database_query"
        )
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# ============================================================================
# Background Tasks
# ============================================================================

async def cleanup_dead_letter_queue_task():
    """
    Background task to clean up dead letter queue.
    
    Runs every 24 hours to remove old entries.
    """
    while True:
        try:
            await asyncio.sleep(24 * 60 * 60)  # Run every 24 hours
            logger.info("Running dead letter queue cleanup...")
            cleanup_dead_letter_queue()
        except Exception as e:
            logger.error(f"Dead letter queue cleanup task error: {e}")


@app.on_event("startup")
async def startup_event():
    """Start background tasks on startup"""
    # Start dead letter queue cleanup task
    asyncio.create_task(cleanup_dead_letter_queue_task())
    logger.info("Started dead letter queue cleanup background task")

# ============================================================================
# Redis Session Cache with Invalidation
# ============================================================================

def cache_session_state(session_id: str, state: dict, ttl: int = 300):
    """
    Cache session state in Redis for fast lookups.
    
    Args:
        session_id: Session ID
        state: Session state dictionary
        ttl: Cache TTL in seconds (default: 5 minutes)
    """
    if redis_client is None:
        logger.debug("Redis not available, skipping session cache")
        return
    
    try:
        cache_key = f"session:{session_id}"
        redis_client.setex(cache_key, ttl, json.dumps(state))
        logger.debug(f"Cached session state: {session_id}")
    except Exception as e:
        logger.error(f"Failed to cache session: {e}")


def get_cached_session_state(session_id: str) -> Optional[dict]:
    """
    Get cached session state from Redis.
    
    Args:
        session_id: Session ID
        
    Returns:
        Session state dictionary or None if not cached
    """
    if redis_client is None:
        return None
    
    try:
        cache_key = f"session:{session_id}"
        cached = redis_client.get(cache_key)
        
        if cached:
            logger.debug(f"Cache hit for session: {session_id}")
            return json.loads(cached)
        
        logger.debug(f"Cache miss for session: {session_id}")
        return None
    except Exception as e:
        logger.error(f"Failed to get cached session: {e}")
        return None


def invalidate_session_cache(session_id: str):
    """
    Invalidate cached session state.
    
    Called when session state changes to prevent stale cache.
    
    Args:
        session_id: Session ID to invalidate
    """
    if redis_client is None:
        return
    
    try:
        cache_key = f"session:{session_id}"
        redis_client.delete(cache_key)
        logger.debug(f"Invalidated session cache: {session_id}")
    except Exception as e:
        logger.error(f"Failed to invalidate session cache: {e}")


def cache_sessions_list(sessions: list, ttl: int = 60):
    """
    Cache list of all sessions.
    
    Args:
        sessions: List of session dictionaries
        ttl: Cache TTL in seconds (default: 1 minute)
    """
    if redis_client is None:
        return
    
    try:
        cache_key = "sessions:all"
        redis_client.setex(cache_key, ttl, json.dumps(sessions))
        logger.debug(f"Cached sessions list ({len(sessions)} sessions)")
    except Exception as e:
        logger.error(f"Failed to cache sessions list: {e}")


def get_cached_sessions_list() -> Optional[list]:
    """
    Get cached list of all sessions.
    
    Returns:
        List of session dictionaries or None if not cached
    """
    if redis_client is None:
        return None
    
    try:
        cache_key = "sessions:all"
        cached = redis_client.get(cache_key)
        
        if cached:
            logger.debug("Cache hit for sessions list")
            return json.loads(cached)
        
        logger.debug("Cache miss for sessions list")
        return None
    except Exception as e:
        logger.error(f"Failed to get cached sessions list: {e}")
        return None


def invalidate_all_sessions_cache():
    """Invalidate cached list of all sessions"""
    if redis_client is None:
        return
    
    try:
        cache_key = "sessions:all"
        redis_client.delete(cache_key)
        logger.debug("Invalidated all sessions cache")
    except Exception as e:
        logger.error(f"Failed to invalidate sessions cache: {e}")

from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=5, max=60),
    reraise=True
)
def destroy_container_with_retry(container_name: str) -> subprocess.CompletedProcess:
    """
    Destroy container with retry logic.
    
    Retries 3 times with exponential backoff: 5s, 10s, 20s
    Raises exception after all retries exhausted.
    """
    return subprocess.run(
        ['./scripts/box-destroy.sh', container_name],
        capture_output=True,
        text=True,
        timeout=30
    )


def add_to_dead_letter_queue(session_id: str, container_name: str, error: str):
    """
    Add failed destruction to dead letter queue for manual intervention.

    Stores in Redis for later review and manual cleanup.
    Auto-cleanup: Entries older than 7 days are automatically removed.
    """
    if redis_client is None:
        logger.error(f"Dead letter queue unavailable (Redis not connected): {session_id}")
        return

    try:
        dead_letter_entry = {
            "session_id": session_id,
            "container_name": container_name,
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "retry_attempts": 3,
            "status": "pending_manual_cleanup",
            "auto_cleanup_at": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        }

        redis_client.lpush(
            "sshbox:dead_letter:destroy",
            json.dumps(dead_letter_entry)
        )

        # Keep only last 1000 entries
        redis_client.ltrim("sshbox:dead_letter:destroy", 0, 999)
        
        # Set expiry on the dead letter queue (7 days)
        redis_client.expire("sshbox:dead_letter:destroy", 7 * 24 * 60 * 60)

        logger.warning(f"Added to dead letter queue: {session_id}")

    except Exception as e:
        logger.error(f"Failed to add to dead letter queue: {e}")


def cleanup_dead_letter_queue():
    """
    Clean up old entries from dead letter queue.
    
    Removes entries older than 7 days.
    Should be called periodically (e.g., daily cron job).
    """
    if redis_client is None:
        return
    
    try:
        now = datetime.now(timezone.utc)
        dlq = redis_client.lrange("sshbox:dead_letter:destroy", 0, -1)
        
        cleaned = []
        for entry_json in dlq:
            try:
                entry = json.loads(entry_json)
                cleanup_at = datetime.fromisoformat(entry.get("auto_cleanup_at", ""))
                
                if cleanup_at.replace(tzinfo=timezone.utc) <= now:
                    # Entry should be cleaned up
                    redis_client.lrem("sshbox:dead_letter:destroy", 1, entry_json)
                    cleaned.append(entry["session_id"])
                    logger.info(f"Cleaned up dead letter entry: {entry['session_id']}")
            except (json.JSONDecodeError, ValueError):
                # Invalid entry, remove it
                redis_client.lrem("sshbox:dead_letter:destroy", 1, entry_json)
        
        if cleaned:
            logger.info(f"Dead letter queue cleanup complete: removed {len(cleaned)} entries")
        
    except Exception as e:
        logger.error(f"Dead letter queue cleanup failed: {e}")


def schedule_destroy(container_name: str, session_id: str, ttl: int):
    """Schedule container destruction after TTL with retry logic and dead letter queue"""

    def destroy_task():
        """Background task to destroy container after TTL with retries"""
        try:
            logger.info(f"Scheduled destruction for session {session_id} in {ttl} seconds")
            time.sleep(ttl)

            logger.info(f"Executing destruction for session {session_id}, container {container_name}")

            # Validate container name to prevent command injection
            if not validate_container_name(container_name):
                logger.error(f"Invalid container name: {container_name}")
                return

            # Use retry logic for destruction
            try:
                result = destroy_container_with_retry(container_name)
                
                if result.returncode != 0:
                    logger.error(f"Destroy script failed for container {container_name}: {result.stderr}")
                    record_error("destroy_script_failed")
                    
                    # Add to dead letter queue after retries exhausted
                    add_to_dead_letter_queue(session_id, container_name, result.stderr)
                else:
                    logger.info(f"Successfully destroyed container {container_name}")
                    record_session_destruction()

            except Exception as retry_error:
                logger.error(f"Destroy failed after all retries for session {session_id}: {retry_error}")
                record_error("destroy_retry_exhausted")
                
                # Add to dead letter queue
                add_to_dead_letter_queue(session_id, container_name, str(retry_error))

            # Update session status in DB
            update_session_status(session_id, 'destroyed')
            logger.info(f"Session {session_id} marked as destroyed in database")

        except subprocess.TimeoutExpired:
            logger.error(f"Destroy script timed out for session {session_id}")
            record_error("destroy_timeout")
            
            # Add to dead letter queue
            add_to_dead_letter_queue(session_id, container_name, "Script timeout")
            
        except Exception as e:
            logger.error(f"Error in destroy task for session {session_id}: {e}")
            record_error("destroy_task_error")
            
            # Add to dead letter queue
            add_to_dead_letter_queue(session_id, container_name, str(e))

    # Start background thread
    thread = threading.Thread(target=destroy_task, daemon=True)
    thread.start()
    return thread


def validate_container_name(name: str) -> bool:
    """Validate container name to prevent command injection"""
    import re
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return False
    if len(name) > 64:
        return False
    return True


def update_session_status(session_id: str, status: str):
    """Update session status in database with proper SQL"""
    try:
        with get_db_cursor() as cur:
            # Use parameterized query - NO f-strings
            # Use timezone-aware datetime (Python 3.12+ compatible)
            query = "UPDATE sessions SET status = ?, ended_at = ? WHERE session_id = ?"
            cur.execute(query, (status, datetime.now(timezone.utc).isoformat(), session_id))
    except Exception as e:
        logger.error(f"Failed to update session status: {e}")


# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/", summary="Root endpoint", tags=["General"])
async def root():
    """Welcome endpoint with API information"""
    return {
        "message": "Welcome to sshBox Gateway",
        "version": "2.0.0",
        "documentation": "/docs",
        "endpoints": {
            "request": "POST /request - Request a new SSH box",
            "sessions": "GET /sessions - List active sessions",
            "destroy": "POST /destroy - Destroy a session",
            "health": "GET /health - Health check",
            "metrics": "GET /metrics - Prometheus metrics"
        }
    }


@app.get("/health", summary="Health check endpoint", tags=["General"], response_model=HealthResponse)
async def health_check():
    """Comprehensive health check endpoint"""
    start_time = time.time()
    
    health_status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "2.0.0",
        "database": "connected",
        "redis": "connected",
        "circuit_breakers": {}
    }
    
    # Check database
    try:
        with get_db_cursor() as cur:
            cur.execute("SELECT 1")
    except Exception as e:
        health_status["database"] = f"error: {str(e)[:50]}"
        health_status["status"] = "degraded"
        logger.warning(f"Database health check failed: {e}")
    
    # Check Redis
    if REDIS_AVAILABLE:
        try:
            redis_client.ping()
        except Exception as e:
            health_status["redis"] = f"error: {str(e)[:50]}"
            health_status["status"] = "degraded"
            logger.warning(f"Redis health check failed: {e}")
    else:
        health_status["redis"] = "not configured"
    
    # Check circuit breakers
    registry = CircuitBreakerRegistry()
    for name, breaker in registry._breakers.items():
        health_status["circuit_breakers"][name] = breaker.state.value
    
    # Record health check timing
    elapsed = time.time() - start_time
    record_timing("health_check_duration", elapsed)
    
    status_code = 200 if health_status["status"] == "healthy" else 503
    return JSONResponse(status_code=status_code, content=health_status)


@app.get("/metrics", summary="Prometheus metrics endpoint", tags=["Monitoring"])
async def get_metrics_endpoint():
    """Return metrics in Prometheus exposition format"""
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from fastapi.responses import Response
    
    try:
        current_metrics = metrics.get_metrics()
        
        # Build Prometheus format metrics
        prom_metrics = []
        
        # Request metrics
        prom_metrics.append('# HELP sshbox_requests_total Total number of HTTP requests')
        prom_metrics.append('# TYPE sshbox_requests_total counter')
        prom_metrics.append(f'sshbox_requests_total{{status="successful"}} {current_metrics["requests"]["successful"]}')
        prom_metrics.append(f'sshbox_requests_total{{status="failed"}} {current_metrics["requests"]["failed"]}')
        
        # Session metrics
        prom_metrics.append('# HELP sshbox_sessions_created Total sessions created')
        prom_metrics.append('# TYPE sshbox_sessions_created counter')
        prom_metrics.append(f'sshbox_sessions_created {current_metrics["sessions"]["created"]}')
        
        prom_metrics.append('# HELP sshbox_sessions_destroyed Total sessions destroyed')
        prom_metrics.append('# TYPE sshbox_sessions_destroyed counter')
        prom_metrics.append(f'sshbox_sessions_destroyed {current_metrics["sessions"]["destroyed"]}')
        
        # Session by profile
        prom_metrics.append('# HELP sshbox_sessions_by_profile Sessions created by profile')
        prom_metrics.append('# TYPE sshbox_sessions_by_profile counter')
        for profile, count in current_metrics["sessions"]["by_profile"].items():
            prom_metrics.append(f'sshbox_sessions_by_profile{{profile="{profile}"}} {count}')
        
        # Performance metrics
        prom_metrics.append('# HELP sshbox_avg_provision_time Average provision time in seconds')
        prom_metrics.append('# TYPE sshbox_avg_provision_time gauge')
        avg_time = current_metrics["performance"].get("avg_provision_time", 0)
        prom_metrics.append(f'sshbox_avg_provision_time {avg_time}')
        
        # Error metrics
        prom_metrics.append('# HELP sshbox_errors_total Total number of errors')
        prom_metrics.append('# TYPE sshbox_errors_total counter')
        for error_type, count in current_metrics["errors"]["by_type"].items():
            prom_metrics.append(f'sshbox_errors_total{{type="{error_type}"}} {count}')
        
        # Circuit breaker metrics
        registry = CircuitBreakerRegistry()
        prom_metrics.append('# HELP sshbox_circuit_breaker_state Circuit breaker state (0=closed, 1=open, 2=half-open)')
        prom_metrics.append('# TYPE sshbox_circuit_breaker_state gauge')
        for name, breaker in registry._breakers.items():
            state_value = {"closed": 0, "open": 1, "half_open": 2}.get(breaker.state.value, 0)
            prom_metrics.append(f'sshbox_circuit_breaker_state{{name="{name}"}} {state_value}')
        
        return Response("\n".join(prom_metrics), media_type=CONTENT_TYPE_LATEST)
        
    except Exception as e:
        logger.error(f"Error generating metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Metrics error: {e}")


@app.post("/request", summary="Request a new SSH box", tags=["Sessions"], response_model=ConnectionResponse)
@limiter.limit("5/minute") if RATE_LIMITING_ENABLED else lambda x: x
async def handle_request(req: Request, request: TokenRequest, background_tasks: BackgroundTasks):
    """
    Request a new ephemeral SSH box
    
    - **token**: HMAC authentication token
    - **pubkey**: SSH public key for authentication
    - **profile**: Session profile (dev, debug, secure-shell, privileged)
    - **ttl**: Time to live in seconds (60-7200)
    """
    start_time = time.time()
    session_id = None
    
    try:
        logger.info(f"Received request for new SSH box, profile: {request.profile}")
        record_request("/request", success=False)  # Will update to success later
        
        # Validate token
        is_valid, error_msg = validate_token(request.token)
        if not is_valid:
            logger.warning(f"Invalid token received: {error_msg}")
            record_request("/request", success=False)
            record_error("token_validation_failed")
            raise TokenValidationError(reason=error_msg)
        
        # Extract TTL from token (use minimum of token TTL and requested TTL)
        token_ttl = int(request.token.split(':')[1])
        effective_ttl = min(token_ttl, request.ttl)

        # Generate session ID with UUID suffix for guaranteed uniqueness
        # Prevents collision under high concurrency (millisecond timestamp + 8 char UUID)
        import uuid
        session_id = f"box_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        container_name = f"box_{session_id}"

        # Validate session ID
        validate_session_id(session_id)
        
        logger.info(
            f"Creating session {session_id} with profile {request.profile}, TTL {effective_ttl}s",
            extra={"session_id": session_id, "profile": request.profile}
        )
        
        # Check circuit breaker
        if provisioning_breaker.state.value == "open":
            raise CircuitBreakerOpenError(
                operation="provisioning",
                retry_after=provisioning_breaker.recovery_timeout
            )
        
        # Call provisioner script with circuit breaker
        try:
            result = provisioning_breaker.call(
                subprocess.run,
                [
                    PROVISIONER_PATH,
                    session_id,
                    request.pubkey,
                    request.profile,
                    str(effective_ttl)
                ],
                capture_output=True,
                text=True,
                timeout=30
            )
        except subprocess.TimeoutExpired:
            logger.error(f"Provisioning timed out for session {session_id}")
            record_error("provisioning_timeout")
            raise ProvisioningError(
                reason="Provisioning timed out",
                session_id=session_id
            )
        
        if result.returncode != 0:
            logger.error(f"Provisioning failed for session {session_id}: {result.stderr}")
            record_error("provisioning_failed")
            raise ProvisioningError(
                reason=f"Provisioning failed: {result.stderr[:200]}",
                session_id=session_id,
                stderr=result.stderr
            )

        # Parse provisioner output
        try:
            connection_info = json.loads(result.stdout.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from provisioner for session {session_id}: {result.stdout}")
            record_error("invalid_provisioner_response")
            raise ProvisioningError(
                reason="Invalid response from provisioner",
                session_id=session_id
            )

        # Validate connection info
        if not all(k in connection_info for k in ['host', 'port', 'user']):
            logger.error(f"Incomplete connection info for session {session_id}")
            raise ProvisioningError(
                reason="Incomplete connection info from provisioner",
                session_id=session_id
            )

        # Add session ID to connection info
        connection_info['session_id'] = session_id
        
        # Store session metadata in DB and cache in Redis
        try:
            with get_db_cursor() as cur:
                # Use parameterized query - NO f-strings
                query = """
                    INSERT INTO sessions
                    (session_id, container_name, ssh_host, ssh_port, ssh_user, profile, ttl, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                cur.execute(query, (
                    session_id,
                    container_name,
                    connection_info.get('host'),
                    connection_info.get('port'),
                    connection_info.get('user'),
                    request.profile,
                    effective_ttl,
                    'active',
                    datetime.now(timezone.utc).isoformat()
                ))
            
            # Cache session state in Redis for fast lookups (TTL: 5 minutes)
            if redis_client:
                session_state = {
                    'session_id': session_id,
                    'container_name': container_name,
                    'host': connection_info.get('host'),
                    'port': connection_info.get('port'),
                    'user': connection_info.get('user'),
                    'profile': request.profile,
                    'status': 'active',
                    'created_at': datetime.now(timezone.utc).isoformat()
                }
                redis_client.setex(
                    f"session:{session_id}",
                    300,  # 5 minute cache
                    json.dumps(session_state)
                )
                
        except Exception as e:
            logger.error(f"Failed to store session metadata: {e}")
            record_error("database_insert_failed")
            raise DatabaseError(
                reason="Failed to store session metadata",
                operation="insert_session"
            )

        logger.info(
            f"Session {session_id} created successfully",
            extra={
                "session_id": session_id,
                "host": connection_info.get('host'),
                "port": connection_info.get('port')
            }
        )

        # Record success metrics
        record_request("/request", success=True)
        record_session_creation(request.profile)
        record_timing("provision_time", time.time() - start_time)
        
        # Schedule destruction
        background_tasks.add_task(schedule_destroy, container_name, session_id, effective_ttl)

        return ConnectionResponse(**connection_info)

    except TokenValidationError as e:
        raise HTTPException(status_code=403, detail=e.to_dict())
    except ProvisioningError as e:
        raise HTTPException(status_code=500, detail=e.to_dict())
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=e.to_dict())
    except CircuitBreakerOpenError as e:
        raise HTTPException(status_code=503, detail=e.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in handle_request: {e}", exc_info=True)
        record_request("/request", success=False)
        record_error("unexpected_error")
        raise HTTPException(
            status_code=500,
            detail={"error": "INTERNAL_ERROR", "message": "An unexpected error occurred"}
        )


@app.get("/sessions", summary="List active sessions", tags=["Sessions"])
@limiter.limit("10/minute") if RATE_LIMITING_ENABLED else lambda x: x
async def list_sessions(req: Request, status_filter: Optional[str] = None):
    """
    List sessions with optional status filter

    - **status**: Filter by status (active, destroyed, ended)
    """
    try:
        # Try Redis cache first for better performance
        cache_key = f"sessions:{status_filter or 'all'}"
        
        if redis_client:
            cached = redis_client.get(cache_key)
            if cached:
                logger.debug(f"Cache hit for {cache_key}")
                return json.loads(cached)
        
        # Cache miss - query database
        with get_db_cursor() as cur:
            if status_filter:
                # Use parameterized query
                query = "SELECT * FROM sessions WHERE status = ? ORDER BY created_at DESC"
                cur.execute(query, (status_filter,))
            else:
                cur.execute("SELECT * FROM sessions ORDER BY created_at DESC")

            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]

            sessions = []
            for row in rows:
                session = dict(zip(columns, row))

                # Calculate time left for active sessions
                if session.get('status') == 'active' and session.get('ttl') and session.get('created_at'):
                    created_at = session['created_at']
                    if isinstance(created_at, str):
                        try:
                            created_at = datetime.fromisoformat(created_at.replace('Z', '').split('.')[0])
                        except ValueError:
                            pass

                    if isinstance(created_at, datetime):
                        expires_at = created_at + timedelta(seconds=session['ttl'])
                        # Use timezone-aware datetime
                        time_left = max(0, int((expires_at - datetime.now(timezone.utc)).total_seconds()))
                        session['time_left'] = time_left

                sessions.append(session)

            result = {"sessions": sessions, "count": len(sessions)}
            
            # Cache result for 1 minute
            if redis_client:
                redis_client.setex(cache_key, 60, json.dumps(result))
            
            return result

    except Exception as e:
        logger.error(f"Error listing sessions: {e}")
        raise HTTPException(status_code=500, detail=f"Error listing sessions: {e}")


@app.post("/destroy", summary="Destroy a specific session", tags=["Sessions"])
@limiter.limit("20/hour") if RATE_LIMITING_ENABLED else lambda x: x
async def destroy_session(req: Request, destroy_request: DestroyRequest):
    """
    Destroy a specific session
    
    - **session_id**: ID of the session to destroy
    """
    try:
        # Validate session ID
        validate_session_id(destroy_request.session_id)
        
        # Get container name from DB
        with get_db_cursor() as cur:
            query = "SELECT container_name, status FROM sessions WHERE session_id = ?"
            cur.execute(query, (destroy_request.session_id,))
            result = cur.fetchone()

            if not result:
                raise SessionNotFoundError(destroy_request.session_id)

            container_name, db_status = result

            if db_status == 'destroyed':
                raise SessionAlreadyDestroyedError(destroy_request.session_id)

        # Validate container name
        if not validate_container_name(container_name):
            logger.error(f"Invalid container name in database: {container_name}")
            raise HTTPException(status_code=500, detail="Invalid container name")

        # Call destroy script
        result = subprocess.run(
            ['./scripts/box-destroy.sh', container_name],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            logger.error(f"Destroy failed for session {destroy_request.session_id}: {result.stderr}")
            raise HTTPException(
                status_code=500,
                detail=f"Destroy failed: {result.stderr[:200]}"
            )

        # Update session status in DB
        update_session_status(destroy_request.session_id, 'destroyed')
        record_session_destruction()
        
        # CACHE INVALIDATION: Invalidate session cache on destroy
        invalidate_session_cache(destroy_request.session_id)
        invalidate_all_sessions_cache()

        logger.info(f"Session {destroy_request.session_id} destroyed successfully")

        return {
            "message": f"Session {destroy_request.session_id} destroyed successfully",
            "session_id": destroy_request.session_id
        }
        
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.to_dict())
    except SessionAlreadyDestroyedError as e:
        raise HTTPException(status_code=400, detail=e.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error destroying session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error destroying session: {e}")


# ============================================================================
# Exception Handlers
# ============================================================================

@app.exception_handler(SSHBoxError)
async def sshbox_error_handler(request: Request, exc: SSHBoxError):
    """Handle custom SSHBox exceptions"""
    logger.warning(f"SSHBox error: {exc.code} - {exc.message}")
    return JSONResponse(
        status_code=400 if "VALIDATION" in exc.code or "NOT_FOUND" in exc.code else 500,
        content=exc.to_dict()
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle unhandled exceptions"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    record_error("unhandled_exception")
    
    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_ERROR",
            "message": "An internal error occurred"
        }
    )


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == '__main__':
    import uvicorn
    
    gateway_port = int(os.environ.get('GATEWAY_PORT', 8080))
    gateway_host = os.environ.get('GATEWAY_HOST', '0.0.0.0')
    
    logger.info(f"Starting sshBox Gateway on {gateway_host}:{gateway_port}")
    
    uvicorn.run(
        app,
        host=gateway_host,
        port=gateway_port,
        log_level="info",
        access_log=True
    )
