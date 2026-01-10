#!/usr/bin/env python3
"""
A proper HTTP gateway for sshBox using FastAPI
"""
import os
import hmac
import hashlib
import time
import json
import subprocess
import threading
from datetime import datetime, timedelta
from typing import Optional, List
import redis
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import sqlite3
import logging
from logging.handlers import RotatingFileHandler
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="sshBox Gateway", description="Gateway for ephemeral SSH boxes")

# Add middleware for security
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Set up logging
logs_dir = os.environ.get('LOGS_DIR', '/tmp/sshbox_logs')
os.makedirs(logs_dir, exist_ok=True)

logger = logging.getLogger("gateway")
logger.setLevel(logging.INFO)

# Create formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# File handler with rotation
log_file = os.path.join(logs_dir, "gateway.log")
file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Configuration
GATEWAY_SECRET = os.environ.get('GATEWAY_SECRET', 'replace-with-secret')
PROVISIONER_PATH = os.environ.get('PROVISIONER_PATH', './scripts/box-provision.sh')

# Connect to Redis for coordination
redis_client = redis.Redis(
    host=os.environ.get('REDIS_HOST', 'localhost'),
    port=int(os.environ.get('REDIS_PORT', 6379)),
    decode_responses=True
)

# Database configuration
DB_TYPE = os.environ.get('DB_TYPE', 'sqlite')  # 'sqlite' or 'postgresql'
SQLITE_PATH = os.environ.get('SQLITE_PATH', '/tmp/sshbox_sessions.db')

# Import connection pooling
from api.connection_pool import get_db_connection as get_pooled_connection

# Connect to database (either SQLite or PostgreSQL)
def get_db_connection():
    if DB_TYPE == 'postgresql':
        import psycopg2
        return psycopg2.connect(
            host=os.environ.get('DB_HOST', 'localhost'),
            database=os.environ.get('DB_NAME', 'sshbox'),
            user=os.environ.get('DB_USER', 'sshbox_user'),
            password=os.environ.get('DB_PASS', 'sshbox_pass')
        )
    else:  # Default to SQLite with connection pooling
        return get_pooled_connection()

class TokenRequest(BaseModel):
    token: str
    pubkey: str
    profile: str = "dev"
    ttl: int = 1800

class DestroyRequest(BaseModel):
    session_id: str

def validate_token(token: str) -> bool:
    """Validate HMAC token with enhanced security checks"""
    try:
        parts = token.split(':')
        if len(parts) != 6:  # profile:ttl:timestamp:recipient_hash:notes_hash:signature
            logger.warning(f"Invalid token format: {token[:20]}...")
            return False

        profile, ttl_str, timestamp_str, recipient_hash, notes_hash, signature = parts

        # Validate TTL is numeric
        try:
            ttl = int(ttl_str)
        except ValueError:
            logger.warning(f"Invalid TTL in token: {token[:20]}...")
            return False

        # Validate timestamp is not too old (prevent replay attacks)
        try:
            token_timestamp = int(timestamp_str)
            current_time = int(time.time())
            if current_time - token_timestamp > 300:  # 5 minutes max validity
                logger.warning(f"Token expired: {token[:20]}...")
                return False
        except ValueError:
            logger.warning(f"Invalid timestamp in token: {token[:20]}...")
            return False

        # Validate profile is in allowed list
        allowed_profiles = ["dev", "debug", "secure-shell", "privileged"]
        if profile not in allowed_profiles:
            logger.warning(f"Invalid profile in token: {profile}")
            return False

        expected_payload = f"{profile}:{ttl_str}:{timestamp_str}:{recipient_hash}:{notes_hash}"
        expected_signature = hmac.new(
            GATEWAY_SECRET.encode(),
            expected_payload.encode(),
            hashlib.sha256
        ).hexdigest()

        is_valid = hmac.compare_digest(signature, expected_signature)
        if not is_valid:
            logger.warning(f"Invalid token signature: {token[:20]}...")

        return is_valid
    except Exception as e:
        logger.error(f"Error validating token: {e}")
        return False

def schedule_destroy(container_name: str, session_id: str, ttl: int):
    """Schedule container destruction after TTL"""
    def destroy_task():
        logger.info(f"Scheduled destruction for session {session_id} in {ttl} seconds")
        time.sleep(ttl)
        try:
            logger.info(f"Executing destruction for session {session_id}, container {container_name}")
            result = subprocess.run(['./scripts/box-destroy.sh', container_name],
                                  capture_output=True, text=True)

            if result.returncode != 0:
                logger.error(f"Destroy script failed for container {container_name}: {result.stderr}")
            else:
                logger.info(f"Successfully destroyed container {container_name}")

            # Update session status in DB
            if DB_TYPE == 'postgresql':
                conn = get_db_connection()
                cur = conn.cursor()

                # Use appropriate placeholder based on DB type
                placeholder = '%s'

                update_query = f"UPDATE sessions SET status = 'destroyed', ended_at = {placeholder} WHERE session_id = {placeholder}"
                cur.execute(update_query, (datetime.utcnow().isoformat(), session_id))
                conn.commit()
                cur.close()
                conn.close()
            else:  # SQLite with connection pooling
                with get_db_connection() as conn:
                    cur = conn.cursor()

                    # Use appropriate placeholder based on DB type
                    placeholder = '?'

                    update_query = f"UPDATE sessions SET status = 'destroyed', ended_at = {placeholder} WHERE session_id = {placeholder}"
                    cur.execute(update_query, (datetime.utcnow().isoformat(), session_id))
                    conn.commit()
                    cur.close()

            logger.info(f"Session {session_id} marked as destroyed in database")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to destroy container {container_name}: {e}")
        except Exception as e:
            logger.error(f"Error updating session status for {session_id}: {e}")

@app.post("/request", summary="Request a new SSH box")
@limiter.limit("5/minute")  # Limit to 5 requests per minute per IP
async def handle_request(request: TokenRequest, background_tasks: BackgroundTasks):
    try:
        logger.info(f"Received request for new SSH box, profile: {request.profile}, session_id: {int(time.time() * 1000000)}")

        # Validate token
        if not validate_token(request.token):
            logger.warning(f"Invalid token received from client")
            raise HTTPException(status_code=403, detail="Invalid token")

        # Extract TTL from token
        ttl = int(request.token.split(':')[1])

        # Generate session ID
        session_id = str(int(time.time() * 1000000))  # microsecond precision
        container_name = f"box_{session_id}"

        logger.info(f"Creating session {session_id} with profile {request.profile}, TTL {ttl}s")

        # Call provisioner script
        result = subprocess.run([
            PROVISIONER_PATH,
            session_id,
            request.pubkey,
            request.profile,
            str(ttl)
        ], capture_output=True, text=True)

        if result.returncode != 0:
            logger.error(f"Provisioning failed for session {session_id}: {result.stderr}")
            raise HTTPException(status_code=500, detail=f"Provisioning failed: {result.stderr}")

        # Parse provisioner output
        try:
            connection_info = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON response from provisioner for session {session_id}: {result.stdout}")
            raise HTTPException(status_code=500, detail="Invalid response from provisioner")

        # Add session ID to connection info
        connection_info['session_id'] = session_id

        # Store session metadata in DB
        if DB_TYPE == 'postgresql':
            conn = get_db_connection()
            cur = conn.cursor()

            # Use appropriate placeholder based on DB type
            placeholder = '%s'

            query = f"""
                INSERT INTO sessions
                (session_id, container_name, ssh_host, ssh_port, ssh_user, profile, ttl, status, created_at)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
            """

            cur.execute(query, (
                session_id,
                container_name,
                connection_info.get('host'),
                connection_info.get('port'),
                connection_info.get('user'),
                request.profile,
                ttl,
                'active',
                datetime.utcnow().isoformat()
            ))
            conn.commit()
            cur.close()
            conn.close()
        else:  # SQLite with connection pooling
            with get_db_connection() as conn:
                cur = conn.cursor()
                placeholder = '?'

                query = f"""
                    INSERT INTO sessions
                    (session_id, container_name, ssh_host, ssh_port, ssh_user, profile, ttl, status, created_at)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                """

                cur.execute(query, (
                    session_id,
                    container_name,
                    connection_info.get('host'),
                    connection_info.get('port'),
                    connection_info.get('user'),
                    request.profile,
                    ttl,
                    'active',
                    datetime.utcnow().isoformat()
                ))
                conn.commit()
                cur.close()

        logger.info(f"Session {session_id} created successfully, host: {connection_info.get('host')}, port: {connection_info.get('port')}")

        # Schedule destruction
        background_tasks.add_task(schedule_destroy, container_name, session_id, ttl)

        return connection_info

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in handle_request: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/sessions", summary="List active sessions")
@limiter.limit("10/minute")  # Limit to 10 requests per minute per IP
async def list_sessions(status: Optional[str] = None):
    try:
        if DB_TYPE == 'postgresql':
            conn = get_db_connection()
            cur = conn.cursor()

            # Use appropriate placeholder based on DB type
            placeholder = '%s'

            if status:
                query = f"SELECT * FROM sessions WHERE status = {placeholder} ORDER BY created_at DESC"
                cur.execute(query, (status,))
            else:
                cur.execute("SELECT * FROM sessions ORDER BY created_at DESC")

            rows = cur.fetchall()

            # Get column names differently based on DB type
            import psycopg2
            columns = [desc[0] for desc in cur.description]

            sessions = []
            for row in rows:
                session = dict(zip(columns, row))
                # Calculate time left
                if session['status'] == 'active' and session['ttl'] and session['created_at']:
                    created_at = session['created_at']
                    # Ensure created_at is a datetime object
                    if isinstance(created_at, str):
                        # Handle different datetime string formats
                        created_at = datetime.fromisoformat(created_at.replace('Z', '').split('.')[0])
                    expires_at = created_at + timedelta(seconds=session['ttl'])
                    time_left = max(0, int((expires_at - datetime.utcnow()).total_seconds()))
                    session['time_left'] = time_left
                sessions.append(session)

            cur.close()
            conn.close()
        else:  # SQLite with connection pooling
            with get_db_connection() as conn:
                cur = conn.cursor()

                # Use appropriate placeholder based on DB type
                placeholder = '?'

                if status:
                    query = f"SELECT * FROM sessions WHERE status = {placeholder} ORDER BY created_at DESC"
                    cur.execute(query, (status,))
                else:
                    cur.execute("SELECT * FROM sessions ORDER BY created_at DESC")

                rows = cur.fetchall()

                # Get column names for SQLite
                columns = [desc[0] for desc in cur.description]

                sessions = []
                for row in rows:
                    session = dict(zip(columns, row))
                    # Calculate time left
                    if session['status'] == 'active' and session['ttl'] and session['created_at']:
                        created_at = session['created_at']
                        # Ensure created_at is a datetime object
                        if isinstance(created_at, str):
                            # Handle different datetime string formats
                            created_at = datetime.fromisoformat(created_at.replace('Z', '').split('.')[0])
                        expires_at = created_at + timedelta(seconds=session['ttl'])
                        time_left = max(0, int((expires_at - datetime.utcnow()).total_seconds()))
                        session['time_left'] = time_left
                    sessions.append(session)

                cur.close()

        return sessions
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/destroy", summary="Destroy a specific session")
@limiter.limit("20/hour")  # Limit to 20 requests per hour per IP
async def destroy_session(destroy_request: DestroyRequest):
    try:
        # Get container name from DB
        if DB_TYPE == 'postgresql':
            conn = get_db_connection()
            cur = conn.cursor()

            # Use appropriate placeholder based on DB type
            placeholder = '%s'

            query = f"SELECT container_name, status FROM sessions WHERE session_id = {placeholder}"
            cur.execute(query, (destroy_request.session_id,))
            result = cur.fetchone()

            if not result:
                raise HTTPException(status_code=404, detail="Session not found")

            container_name, status = result

            if status == 'destroyed':
                return {"message": "Session already destroyed"}

            # Call destroy script
            result = subprocess.run(['./scripts/box-destroy.sh', container_name],
                                  capture_output=True, text=True)

            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=f"Destroy failed: {result.stderr}")

            # Update session status in DB
            update_query = f"UPDATE sessions SET status = 'destroyed', ended_at = {placeholder} WHERE session_id = {placeholder}"
            cur.execute(update_query, (datetime.utcnow().isoformat(), destroy_request.session_id))
            conn.commit()
            cur.close()
            conn.close()
        else:  # SQLite with connection pooling
            with get_db_connection() as conn:
                cur = conn.cursor()

                # Use appropriate placeholder based on DB type
                placeholder = '?'

                query = f"SELECT container_name, status FROM sessions WHERE session_id = {placeholder}"
                cur.execute(query, (destroy_request.session_id,))
                result = cur.fetchone()

                if not result:
                    raise HTTPException(status_code=404, detail="Session not found")

                container_name, status = result

                if status == 'destroyed':
                    return {"message": "Session already destroyed"}

                # Call destroy script
                result = subprocess.run(['./scripts/box-destroy.sh', container_name],
                                      capture_output=True, text=True)

                if result.returncode != 0:
                    raise HTTPException(status_code=500, detail=f"Destroy failed: {result.stderr}")

                # Update session status in DB
                update_query = f"UPDATE sessions SET status = 'destroyed', ended_at = {placeholder} WHERE session_id = {placeholder}"
                cur.execute(update_query, (datetime.utcnow().isoformat(), destroy_request.session_id))
                conn.commit()
                cur.close()

        return {"message": f"Session {destroy_request.session_id} destroyed successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health", summary="Health check endpoint")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@app.get("/", summary="Root endpoint")
async def root():
    return {
        "message": "Welcome to sshBox Gateway",
        "endpoints": [
            "/request - Request a new SSH box",
            "/sessions - List active sessions", 
            "/destroy - Destroy a session",
            "/health - Health check"
        ]
    }

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=int(os.environ.get('GATEWAY_PORT', 8080)),
        log_level="info"
    )