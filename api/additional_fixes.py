"""
SSH Boxes - Additional Fixes

Fixes:
1. DLQ metrics for Prometheus
2. Cache warming on session creation
3. Session export/import API
"""

import json
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, HTTPException, Depends, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# ============================================================================
# Fix 1: DLQ Metrics for Prometheus
# ============================================================================

class DLQMetrics:
    """Dead Letter Queue metrics"""
    
    def __init__(self):
        self.dlq_entries_total = 0
        self.dlq_cleaned_total = 0
        self.dlq_current_size = 0
        self.dlq_last_cleanup = None
        self.dlq_oldest_entry_age_seconds = 0
    
    def record_entry(self, entry: dict):
        """Record entry added to DLQ"""
        self.dlq_entries_total += 1
        self.dlq_current_size += 1
        logger.debug(f"DLQ entry added: {entry.get('session_id')}")
    
    def record_cleanup(self, count: int):
        """Record DLQ cleanup"""
        self.dlq_cleaned_total += count
        self.dlq_current_size = max(0, self.dlq_current_size - count)
        self.dlq_last_cleanup = datetime.now(timezone.utc).isoformat()
        logger.info(f"DLQ cleanup: removed {count} entries")
    
    def update_size(self, size: int):
        """Update current DLQ size"""
        self.dlq_current_size = size
    
    def update_oldest_age(self, age_seconds: float):
        """Update oldest entry age"""
        self.dlq_oldest_entry_age_seconds = age_seconds
    
    def get_metrics(self) -> dict:
        """Get all DLQ metrics"""
        return {
            "dlq_entries_total": self.dlq_entries_total,
            "dlq_cleaned_total": self.dlq_cleaned_total,
            "dlq_current_size": self.dlq_current_size,
            "dlq_last_cleanup": self.dlq_last_cleanup,
            "dlq_oldest_entry_age_seconds": self.dlq_oldest_entry_age_seconds
        }
    
    def to_prometheus(self) -> List[str]:
        """Convert to Prometheus format"""
        lines = []
        
        lines.append('# HELP sshbox_dlq_entries_total Total entries added to dead letter queue')
        lines.append('# TYPE sshbox_dlq_entries_total counter')
        lines.append(f'sshbox_dlq_entries_total {self.dlq_entries_total}')
        
        lines.append('# HELP sshbox_dlq_cleaned_total Total entries cleaned from DLQ')
        lines.append('# TYPE sshbox_dlq_cleaned_total counter')
        lines.append(f'sshbox_dlq_cleaned_total {self.dlq_cleaned_total}')
        
        lines.append('# HELP sshbox_dlq_size Current dead letter queue size')
        lines.append('# TYPE sshbox_dlq_size gauge')
        lines.append(f'sshbox_dlq_size {self.dlq_current_size}')
        
        lines.append('# HELP sshbox_dlq_oldest_entry_age_seconds Age of oldest DLQ entry')
        lines.append('# TYPE sshbox_dlq_oldest_entry_age_seconds gauge')
        lines.append(f'sshbox_dlq_oldest_entry_age_seconds {self.dlq_oldest_entry_age_seconds}')
        
        return lines


# Global DLQ metrics instance
dlq_metrics = DLQMetrics()


# ============================================================================
# Fix 2: Cache Warming on Session Creation
# ============================================================================

def warm_session_cache(
    redis_client,
    session_id: str,
    session_data: dict,
    ttl: int = 300
):
    """
    Warm cache with session data and related lookups.
    
    Args:
        redis_client: Redis client instance
        session_id: Session ID
        session_data: Complete session data including container info
        ttl: Cache TTL in seconds
    """
    if redis_client is None:
        logger.debug("Redis not available, skipping cache warming")
        return
    
    try:
        # 1. Cache session state
        cache_key = f"session:{session_id}"
        redis_client.setex(cache_key, ttl, json.dumps(session_data))
        logger.debug(f"Cached session state: {session_id}")
        
        # 2. Cache container -> session lookup
        container_name = session_data.get('container_name')
        if container_name:
            container_key = f"container:{container_name}:session"
            redis_client.setex(container_key, ttl, session_id)
            logger.debug(f"Cached container lookup: {container_name} -> {session_id}")
        
        # 3. Cache user's sessions list
        user_id = session_data.get('user_id')
        if user_id:
            user_sessions_key = f"user:{user_id}:sessions"
            existing = redis_client.get(user_sessions_key)
            session_list = json.loads(existing) if existing else []
            
            if session_id not in session_list:
                session_list.append(session_id)
                redis_client.setex(user_sessions_key, ttl, json.dumps(session_list))
                logger.debug(f"Added session to user list: {user_id}")
        
        # 4. Cache profile -> sessions lookup
        profile = session_data.get('profile')
        if profile:
            profile_key = f"profile:{profile}:sessions"
            existing = redis_client.get(profile_key)
            profile_list = json.loads(existing) if existing else []
            
            if session_id not in profile_list:
                profile_list.append(session_id)
                redis_client.setex(profile_key, ttl, json.dumps(profile_list))
                logger.debug(f"Added session to profile list: {profile}")
        
        logger.info(f"Cache warmed for session {session_id}")
        
    except Exception as e:
        logger.error(f"Failed to warm session cache: {e}")


def invalidate_extended_cache(
    redis_client,
    session_id: str,
    user_id: Optional[str] = None,
    profile: Optional[str] = None
):
    """
    Extended cache invalidation including related lookups.
    
    Args:
        redis_client: Redis client instance
        session_id: Session ID to invalidate
        user_id: Optional user ID for invalidating user list
        profile: Optional profile for invalidating profile list
    """
    if redis_client is None:
        return
    
    try:
        # Invalidate session cache
        redis_client.delete(f"session:{session_id}")
        logger.debug(f"Invalidated session cache: {session_id}")
        
        # Invalidate container lookup
        # (would need container_name from session data)
        
        # Invalidate from user's list
        if user_id:
            user_sessions_key = f"user:{user_id}:sessions"
            existing = redis_client.get(user_sessions_key)
            if existing:
                session_list = json.loads(existing)
                if session_id in session_list:
                    session_list.remove(session_id)
                    redis_client.setex(user_sessions_key, 60, json.dumps(session_list))
                    logger.debug(f"Removed from user list: {user_id}")
        
        # Invalidate from profile list
        if profile:
            profile_key = f"profile:{profile}:sessions"
            existing = redis_client.get(profile_key)
            if existing:
                session_list = json.loads(existing)
                if session_id in session_list:
                    session_list.remove(session_id)
                    redis_client.setex(profile_key, 60, json.dumps(session_list))
                    logger.debug(f"Removed from profile list: {profile}")
        
        # Invalidate sessions list cache
        redis_client.delete("sessions:all")
        logger.debug("Invalidated sessions list cache")
        
    except Exception as e:
        logger.error(f"Failed to invalidate extended cache: {e}")


# ============================================================================
# Fix 3: Session Export/Import API
# ============================================================================

router = APIRouter()


@router.get("/sessions/{session_id}/export", tags=["Sessions"])
async def export_session(session_id: str, format: str = "json"):
    """
    Export session data and metadata.
    
    Args:
        session_id: Session to export
        format: Export format (json, csv, asciinema)
    """
    from gateway_fastapi import get_db_cursor, validate_session_id
    
    try:
        validate_session_id(session_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Get session data
    with get_db_cursor() as cur:
        cur.execute("""
            SELECT * FROM sessions WHERE session_id = %s
        """, (session_id,))
        row = cur.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        
        # Convert to dict
        columns = [desc[0] for desc in cur.description]
        session = dict(zip(columns, row))
    
    if format == "json":
        return {
            "session": session,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "format": "json"
        }
    
    elif format == "csv":
        import csv
        import io
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Header
        writer.writerow(session.keys())
        # Data
        writer.writerow(session.values())
        
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=session_{session_id}.csv"
            }
        )
    
    elif format == "asciinema":
        # Export recording if available
        from session_recorder import get_recording
        
        recording = get_recording(session_id)
        if recording:
            return Response(
                content=recording,
                media_type="application/json",
                headers={
                    "Content-Disposition": f"attachment; filename=session_{session_id}.cast"
                }
            )
        else:
            raise HTTPException(status_code=404, detail="Recording not found")
    
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported export format: {format}")


@router.get("/sessions/{session_id}/export/url", tags=["Sessions"])
async def get_export_url(
    session_id: str,
    expiry_seconds: int = 3600
):
    """
    Get pre-signed download URL for session export.
    
    Args:
        session_id: Session to export
        expiry_seconds: URL expiry time in seconds
    """
    # For S3-backed storage, generate pre-signed URL
    # For local storage, generate temporary download token
    
    from gateway_fastapi import get_db_cursor
    
    with get_db_cursor() as cur:
        cur.execute("""
            SELECT container_name, user_id FROM sessions 
            WHERE session_id = %s
        """, (session_id,))
        row = cur.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Session not found")
        
        container_name, user_id = row
    
    # Generate temporary download token
    import secrets
    token = secrets.token_urlsafe(32)
    
    # Store token with expiry (in Redis if available, else in-memory)
    token_data = {
        "session_id": session_id,
        "user_id": user_id,
        "expires_at": datetime.now(timezone.utc).timestamp() + expiry_seconds
    }
    
    # In production, store in Redis with TTL
    # For now, just return the token
    download_url = f"/api/download/{token}"
    
    return {
        "download_url": download_url,
        "expires_in_seconds": expiry_seconds,
        "session_id": session_id
    }


@router.post("/sessions/import", tags=["Sessions"])
async def import_session(
    file: dict,  # In real implementation: UploadFile = File(...)
    user_id: str
):
    """
    Import session from exported file.
    
    Args:
        file: Exported session file (JSON)
        user_id: User importing the session
    """
    # In real implementation:
    # content = await file.read()
    # session_data = json.loads(content)
    
    session_data = file  # For now, accept JSON directly
    
    # Validate required fields
    required_fields = ["session_id", "user_id", "container_name", "status"]
    for field in required_fields:
        if field not in session_data:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required field: {field}"
            )
    
    # Generate new session ID (don't reuse old ID)
    import secrets
    new_session_id = f"imported-{secrets.token_hex(8)}"
    
    # Create session with imported data
    with get_db_cursor() as cur:
        cur.execute("""
            INSERT INTO sessions 
            (session_id, user_id, container_name, status, created_at, profile)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            new_session_id,
            user_id,
            session_data["container_name"],
            "active",
            datetime.now(timezone.utc),
            session_data.get("profile", "default")
        ))
    
    return {
        "message": "Session imported successfully",
        "new_session_id": new_session_id,
        "original_session_id": session_data["session_id"]
    }


@router.get("/sessions/export/stats", tags=["Sessions"])
async def get_export_stats():
    """Get export/import statistics"""
    from gateway_fastapi import get_db_cursor
    
    with get_db_cursor() as cur:
        # Count exports (would need exports table in production)
        # Count imports (would need imports table in production)
        
        return {
            "total_sessions": 0,  # Would query database
            "exportable_sessions": 0,
            "imported_sessions": 0
        }


# ============================================================================
# Integration
# ============================================================================

def include_router(app):
    """Include export/import router in main app"""
    app.include_router(router, prefix="/api", tags=["Session Export/Import"])


def patch_gateway_functions():
    """
    Patch gateway_fastapi functions to use enhanced cache warming/invalidation.
    
    Call this in gateway_fastapi.py startup or import this module.
    """
    import gateway_fastapi
    
    # Wrap cache_session_state to also warm related caches
    original_cache = gateway_fastapi.cache_session_state
    
    def enhanced_cache_session_state(session_id: str, state: dict, ttl: int = 300):
        original_cache(session_id, state, ttl)
        warm_session_cache(gateway_fastapi.redis_client, session_id, state, ttl)
    
    gateway_fastapi.cache_session_state = enhanced_cache_session_state
    
    # Wrap invalidate_session_cache to also invalidate related caches
    original_invalidate = gateway_fastapi.invalidate_session_cache
    
    def enhanced_invalidate_session_cache(session_id: str):
        # Get session data first for extended invalidation
        user_id = None
        profile = None
        
        try:
            with gateway_fastapi.get_db_cursor() as cur:
                cur.execute("""
                    SELECT user_id, profile FROM sessions 
                    WHERE session_id = %s
                """, (session_id,))
                row = cur.fetchone()
                if row:
                    user_id, profile = row
        except Exception:
            pass
        
        original_invalidate(session_id)
        invalidate_extended_cache(
            gateway_fastapi.redis_client,
            session_id,
            user_id,
            profile
        )
    
    gateway_fastapi.invalidate_session_cache = enhanced_invalidate_session_cache
    
    logger.info("Enhanced cache warming/invalidation patched into gateway_fastapi")
