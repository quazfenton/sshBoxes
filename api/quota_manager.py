#!/usr/bin/env python3
"""
Quota Management for sshBox
Track and enforce usage limits per user, IP, or organization
"""
import os
import sys
import sqlite3
import redis
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path
from dataclasses import dataclass, field
from contextlib import contextmanager
import threading

from api.exceptions import QuotaExceededError, DatabaseError, InvalidInputError
from api.logging_config import setup_logging

logger = setup_logging("quota_manager")


@dataclass
class QuotaLimit:
    """Quota limit definition"""
    max_sessions: int = 10
    max_concurrent_sessions: int = 5
    max_daily_sessions: int = 50
    max_weekly_sessions: int = 200
    max_session_ttl: int = 7200  # 2 hours
    max_cpu_per_session: float = 2.0
    max_memory_per_session: str = "4Gi"
    max_daily_cpu_hours: float = 24.0
    enabled: bool = True


@dataclass
class UsageStats:
    """Current usage statistics"""
    concurrent_sessions: int = 0
    daily_sessions: int = 0
    weekly_sessions: int = 0
    total_sessions: int = 0
    daily_cpu_hours: float = 0.0
    last_session_created: Optional[str] = None
    last_session_destroyed: Optional[str] = None


class QuotaManager:
    """
    Manage and enforce user quotas with Redis caching for performance
    
    Usage:
        quota_mgr = QuotaManager()
        
        # Check if user can create a session
        result = quota_mgr.check_quota("user@example.com", requested_ttl=1800)
        if not result["allowed"]:
            raise QuotaExceededError(...)
        
        # Record session creation
        quota_mgr.record_usage("user@example.com", session_id, "session_created")
    """
    
    # Default quotas by role
    DEFAULT_QUOTAS = {
        "default": QuotaLimit(
            max_sessions=10,
            max_concurrent_sessions=5,
            max_daily_sessions=50,
            max_weekly_sessions=200,
            max_session_ttl=7200,
        ),
        "premium": QuotaLimit(
            max_sessions=50,
            max_concurrent_sessions=20,
            max_daily_sessions=200,
            max_weekly_sessions=1000,
            max_session_ttl=14400,
        ),
        "admin": QuotaLimit(
            max_sessions=100,
            max_concurrent_sessions=50,
            max_daily_sessions=500,
            max_weekly_sessions=2000,
            max_session_ttl=28800,
        ),
        "trial": QuotaLimit(
            max_sessions=3,
            max_concurrent_sessions=1,
            max_daily_sessions=10,
            max_weekly_sessions=30,
            max_session_ttl=1800,
        ),
    }
    
    def __init__(
        self,
        db_path: str = "/var/lib/sshbox/quotas.db",
        redis_client: Optional[redis.Redis] = None,
        cache_ttl_seconds: int = 60
    ):
        """
        Initialize quota manager
        
        Args:
            db_path: Path to SQLite database for persistent storage
            redis_client: Optional Redis client for caching
            cache_ttl_seconds: How long to cache quota data in Redis
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.redis = redis_client
        self.cache_ttl = cache_ttl_seconds
        self._local = threading.local()
        
        logger.info(f"QuotaManager initialized with db_path={db_path}")
        self._init_db()
    
    @property
    def _db_connection(self):
        """Get thread-local database connection"""
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=30.0
            )
            self._local.connection.execute("PRAGMA journal_mode=WAL")
            self._local.connection.row_factory = sqlite3.Row
        return self._local.connection
    
    @contextmanager
    def _get_cursor(self):
        """Context manager for database cursor"""
        conn = self._db_connection
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise DatabaseError(reason=str(e), operation="quota_operation")
        finally:
            cursor.close()
    
    def _init_db(self):
        """Initialize quota database with required tables"""
        with self._get_cursor() as cur:
            # User quotas table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_quotas (
                    user_id TEXT PRIMARY KEY,
                    role TEXT DEFAULT 'default',
                    max_sessions INTEGER DEFAULT 10,
                    max_concurrent_sessions INTEGER DEFAULT 5,
                    max_daily_sessions INTEGER DEFAULT 50,
                    max_weekly_sessions INTEGER DEFAULT 200,
                    max_session_ttl INTEGER DEFAULT 7200,
                    custom_limits TEXT,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Usage tracking table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS usage_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_id TEXT,
                    action TEXT NOT NULL,
                    metadata TEXT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES user_quotas(user_id)
                )
            """)
            
            # Organization quotas table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS org_quotas (
                    org_id TEXT PRIMARY KEY,
                    max_total_sessions INTEGER DEFAULT 100,
                    max_total_concurrent INTEGER DEFAULT 50,
                    max_daily_cpu_hours REAL DEFAULT 100.0,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # User-organization mapping
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_org_mapping (
                    user_id TEXT,
                    org_id TEXT,
                    role TEXT DEFAULT 'member',
                    PRIMARY KEY (user_id, org_id),
                    FOREIGN KEY (user_id) REFERENCES user_quotas(user_id),
                    FOREIGN KEY (org_id) REFERENCES org_quotas(org_id)
                )
            """)
            
            # Create indexes for performance
            cur.execute("CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_tracking(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage_tracking(timestamp)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_usage_action ON usage_tracking(action)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_user_org ON user_org_mapping(user_id)")
            
            logger.info("Quota database initialized successfully")
    
    def set_user_quota(
        self,
        user_id: str,
        role: str = None,
        max_sessions: int = None,
        max_concurrent_sessions: int = None,
        max_daily_sessions: int = None,
        max_weekly_sessions: int = None,
        max_session_ttl: int = None,
        custom_limits: Dict = None,
        enabled: bool = True
    ) -> bool:
        """
        Set or update user quota
        
        Args:
            user_id: User identifier
            role: Quota role (default, premium, admin, trial)
            max_sessions: Override max total sessions
            max_concurrent_sessions: Override max concurrent sessions
            max_daily_sessions: Override max daily sessions
            max_weekly_sessions: Override max weekly sessions
            max_session_ttl: Override max session TTL in seconds
            custom_limits: Custom limits as JSON
            enabled: Whether quota enforcement is enabled
        
        Returns:
            True if successful
        """
        # Validate user_id
        if not user_id or len(user_id) > 256:
            raise InvalidInputError(field="user_id", reason="Invalid user ID")
        
        # If role is specified, use role defaults
        if role:
            if role not in self.DEFAULT_QUOTAS:
                raise InvalidInputError(
                    field="role",
                    reason=f"Unknown role: {role}. Available: {list(self.DEFAULT_QUOTAS.keys())}"
                )
            role_quota = self.DEFAULT_QUOTAS[role]
            max_sessions = max_sessions or role_quota.max_sessions
            max_concurrent_sessions = max_concurrent_sessions or role_quota.max_concurrent_sessions
            max_daily_sessions = max_daily_sessions or role_quota.max_daily_sessions
            max_weekly_sessions = max_weekly_sessions or role_quota.max_weekly_sessions
            max_session_ttl = max_session_ttl or role_quota.max_session_ttl
        
        with self._get_cursor() as cur:
            # Check if user exists
            cur.execute("SELECT user_id FROM user_quotas WHERE user_id = ?", (user_id,))
            existing = cur.fetchone()
            
            if existing:
                # Update existing quota
                cur.execute("""
                    UPDATE user_quotas SET
                        role = COALESCE(?, role),
                        max_sessions = COALESCE(?, max_sessions),
                        max_concurrent_sessions = COALESCE(?, max_concurrent_sessions),
                        max_daily_sessions = COALESCE(?, max_daily_sessions),
                        max_weekly_sessions = COALESCE(?, max_weekly_sessions),
                        max_session_ttl = COALESCE(?, max_session_ttl),
                        custom_limits = ?,
                        enabled = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                """, (
                    role, max_sessions, max_concurrent_sessions, max_daily_sessions,
                    max_weekly_sessions, max_session_ttl,
                    json.dumps(custom_limits) if custom_limits else None,
                    1 if enabled else 0,
                    user_id
                ))
            else:
                # Insert new quota
                cur.execute("""
                    INSERT INTO user_quotas
                    (user_id, role, max_sessions, max_concurrent_sessions, max_daily_sessions,
                     max_weekly_sessions, max_session_ttl, custom_limits, enabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    user_id, role or 'default',
                    max_sessions or self.DEFAULT_QUOTAS['default'].max_sessions,
                    max_concurrent_sessions or self.DEFAULT_QUOTAS['default'].max_concurrent_sessions,
                    max_daily_sessions or self.DEFAULT_QUOTAS['default'].max_daily_sessions,
                    max_weekly_sessions or self.DEFAULT_QUOTAS['default'].max_weekly_sessions,
                    max_session_ttl or self.DEFAULT_QUOTAS['default'].max_session_ttl,
                    json.dumps(custom_limits) if custom_limits else None,
                    1 if enabled else 0
                ))
            
            # Invalidate cache
            self._invalidate_cache(user_id)
            
            logger.info(f"Set quota for user {user_id}: role={role}, max_sessions={max_sessions}")
            return True
    
    def check_quota(
        self,
        user_id: str,
        requested_ttl: int = 1800,
        org_id: str = None
    ) -> Dict[str, Any]:
        """
        Check if user can create a new session
        
        Args:
            user_id: User identifier
            requested_ttl: Requested session TTL in seconds
            org_id: Optional organization ID for org-level quota check
        
        Returns:
            {"allowed": bool, "reason": str, "current_usage": UsageStats, "quota_limit": QuotaLimit}
        """
        # Validate inputs
        if not user_id:
            return {"allowed": False, "reason": "User ID is required"}
        
        if requested_ttl <= 0:
            return {"allowed": False, "reason": "TTL must be positive"}
        
        # Get user quota
        quota = self._get_user_quota(user_id)
        if not quota.enabled:
            return {"allowed": True, "reason": "Quota enforcement disabled", "quota_limit": quota}
        
        # Check TTL limit
        if requested_ttl > quota.max_session_ttl:
            return {
                "allowed": False,
                "reason": f"Requested TTL ({requested_ttl}s) exceeds maximum allowed ({quota.max_session_ttl}s)",
                "quota_limit": quota,
                "current_usage": self._get_usage_stats(user_id)
            }
        
        # Get current usage
        usage = self._get_usage_stats(user_id)
        
        # Check concurrent sessions
        if usage.concurrent_sessions >= quota.max_concurrent_sessions:
            return {
                "allowed": False,
                "reason": f"Maximum concurrent sessions ({quota.max_concurrent_sessions}) reached",
                "quota_limit": quota,
                "current_usage": usage
            }
        
        # Check daily sessions
        if usage.daily_sessions >= quota.max_daily_sessions:
            return {
                "allowed": False,
                "reason": f"Daily session limit ({quota.max_daily_sessions}) reached",
                "quota_limit": quota,
                "current_usage": usage
            }
        
        # Check weekly sessions
        if usage.weekly_sessions >= quota.max_weekly_sessions:
            return {
                "allowed": False,
                "reason": f"Weekly session limit ({quota.max_weekly_sessions}) reached",
                "quota_limit": quota,
                "current_usage": usage
            }
        
        # Check organization quota if specified
        if org_id:
            org_result = self._check_org_quota(org_id, user_id)
            if not org_result["allowed"]:
                return org_result
        
        return {
            "allowed": True,
            "reason": "OK",
            "quota_limit": quota,
            "current_usage": usage
        }
    
    def _get_user_quota(self, user_id: str) -> QuotaLimit:
        """Get quota limit for user"""
        # Try cache first
        cache_key = f"quota:{user_id}"
        if self.redis:
            cached = self.redis.get(cache_key)
            if cached:
                data = json.loads(cached)
                return QuotaLimit(**data)
        
        # Get from database
        with self._get_cursor() as cur:
            cur.execute("SELECT * FROM user_quotas WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
        
        if not row:
            # Return default quota
            return self.DEFAULT_QUOTAS['default']
        
        quota = QuotaLimit(
            max_sessions=row['max_sessions'],
            max_concurrent_sessions=row['max_concurrent_sessions'],
            max_daily_sessions=row['max_daily_sessions'],
            max_weekly_sessions=row['max_weekly_sessions'],
            max_session_ttl=row['max_session_ttl'],
            enabled=bool(row['enabled'])
        )
        
        # Parse custom limits
        if row['custom_limits']:
            custom = json.loads(row['custom_limits'])
            for key, value in custom.items():
                if hasattr(quota, key):
                    setattr(quota, key, value)
        
        # Cache the result
        if self.redis:
            self.redis.setex(
                cache_key,
                self.cache_ttl,
                json.dumps({
                    'max_sessions': quota.max_sessions,
                    'max_concurrent_sessions': quota.max_concurrent_sessions,
                    'max_daily_sessions': quota.max_daily_sessions,
                    'max_weekly_sessions': quota.max_weekly_sessions,
                    'max_session_ttl': quota.max_session_ttl,
                    'enabled': quota.enabled
                })
            )
        
        return quota
    
    def _get_usage_stats(self, user_id: str) -> UsageStats:
        """Get current usage statistics for user"""
        now = datetime.utcnow()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = now - timedelta(days=now.weekday())
        week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
        
        with self._get_cursor() as cur:
            # Count concurrent (active) sessions
            cur.execute("""
                SELECT COUNT(*) FROM usage_tracking ut
                WHERE ut.user_id = ? AND ut.action = 'session_created'
                AND NOT EXISTS (
                    SELECT 1 FROM usage_tracking ut2
                    WHERE ut2.user_id = ut.user_id
                    AND ut2.session_id = ut.session_id
                    AND ut2.action = 'session_destroyed'
                )
            """, (user_id,))
            concurrent = cur.fetchone()[0]
            
            # Count daily sessions
            cur.execute("""
                SELECT COUNT(*) FROM usage_tracking
                WHERE user_id = ? AND action = 'session_created'
                AND timestamp >= ?
            """, (user_id, today_start.isoformat()))
            daily = cur.fetchone()[0]
            
            # Count weekly sessions
            cur.execute("""
                SELECT COUNT(*) FROM usage_tracking
                WHERE user_id = ? AND action = 'session_created'
                AND timestamp >= ?
            """, (user_id, week_start.isoformat()))
            weekly = cur.fetchone()[0]
            
            # Count total sessions
            cur.execute("""
                SELECT COUNT(*) FROM usage_tracking
                WHERE user_id = ? AND action = 'session_created'
            """, (user_id,))
            total = cur.fetchone()[0]
            
            # Get last session created
            cur.execute("""
                SELECT timestamp FROM usage_tracking
                WHERE user_id = ? AND action = 'session_created'
                ORDER BY timestamp DESC LIMIT 1
            """, (user_id,))
            row = cur.fetchone()
            last_created = row[0] if row else None
            
            # Get last session destroyed
            cur.execute("""
                SELECT timestamp FROM usage_tracking
                WHERE user_id = ? AND action = 'session_destroyed'
                ORDER BY timestamp DESC LIMIT 1
            """, (user_id,))
            row = cur.fetchone()
            last_destroyed = row[0] if row else None
        
        return UsageStats(
            concurrent_sessions=concurrent,
            daily_sessions=daily,
            weekly_sessions=weekly,
            total_sessions=total,
            last_session_created=last_created,
            last_session_destroyed=last_destroyed
        )
    
    def _check_org_quota(self, org_id: str, user_id: str) -> Dict[str, Any]:
        """Check organization-level quota"""
        with self._get_cursor() as cur:
            cur.execute("SELECT * FROM org_quotas WHERE org_id = ? AND enabled = 1", (org_id,))
            org_row = cur.fetchone()
            
            if not org_row:
                return {"allowed": True, "reason": "No org quota defined"}
            
            # Check org concurrent sessions
            cur.execute("""
                SELECT COUNT(DISTINCT ut.session_id)
                FROM usage_tracking ut
                JOIN user_org_mapping uom ON ut.user_id = uom.user_id
                WHERE uom.org_id = ? AND ut.action = 'session_created'
                AND NOT EXISTS (
                    SELECT 1 FROM usage_tracking ut2
                    WHERE ut2.user_id = ut.user_id
                    AND ut2.session_id = ut.session_id
                    AND ut2.action = 'session_destroyed'
                )
            """, (org_id,))
            org_concurrent = cur.fetchone()[0]
            
            if org_concurrent >= org_row['max_total_concurrent']:
                return {
                    "allowed": False,
                    "reason": f"Organization concurrent session limit ({org_row['max_total_concurrent']}) reached"
                }
        
        return {"allowed": True, "reason": "OK"}
    
    def record_usage(
        self,
        user_id: str,
        session_id: str,
        action: str,
        metadata: Dict = None
    ) -> bool:
        """
        Record usage event
        
        Args:
            user_id: User identifier
            session_id: Session identifier
            action: Action type (session_created, session_destroyed, etc.)
            metadata: Optional metadata
        
        Returns:
            True if successful
        """
        with self._get_cursor() as cur:
            cur.execute("""
                INSERT INTO usage_tracking (user_id, session_id, action, metadata)
                VALUES (?, ?, ?, ?)
            """, (user_id, session_id, action, json.dumps(metadata) if metadata else None))
        
        # Invalidate cache
        self._invalidate_cache(user_id)
        
        logger.debug(f"Recorded usage: user={user_id}, session={session_id}, action={action}")
        return True
    
    def _invalidate_cache(self, user_id: str):
        """Invalidate cached quota data"""
        if self.redis:
            self.redis.delete(f"quota:{user_id}")
    
    def get_usage_report(
        self,
        user_id: str,
        days: int = 7
    ) -> Dict[str, Any]:
        """
        Get usage report for user
        
        Args:
            user_id: User identifier
            days: Number of days to include in report
        
        Returns:
            Usage report dictionary
        """
        quota = self._get_user_quota(user_id)
        usage = self._get_usage_stats(user_id)
        
        # Get session duration stats
        with self._get_cursor() as cur:
            cur.execute("""
                SELECT AVG(
                    (julianday(ut2.timestamp) - julianday(ut.timestamp)) * 24
                )
                FROM usage_tracking ut
                LEFT JOIN usage_tracking ut2 ON
                    ut.session_id = ut2.session_id AND
                    ut2.action = 'session_destroyed'
                WHERE ut.user_id = ? AND ut.action = 'session_created'
                AND ut.timestamp >= datetime('now', '-' || ? || ' days')
            """, (user_id, days))
            avg_duration_hours = cur.fetchone()[0] or 0
        
        return {
            "user_id": user_id,
            "quota": {
                "role": "default",
                "max_sessions": quota.max_sessions,
                "max_concurrent_sessions": quota.max_concurrent_sessions,
                "max_daily_sessions": quota.max_daily_sessions,
                "max_weekly_sessions": quota.max_weekly_sessions,
                "max_session_ttl": quota.max_session_ttl
            },
            "usage": {
                "concurrent_sessions": usage.concurrent_sessions,
                "daily_sessions": usage.daily_sessions,
                "weekly_sessions": usage.weekly_sessions,
                "total_sessions": usage.total_sessions,
                "avg_session_duration_hours": round(avg_duration_hours, 2)
            },
            "period_days": days
        }
    
    def list_users_with_quotas(
        self,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """List all users with their quotas"""
        with self._get_cursor() as cur:
            cur.execute("""
                SELECT * FROM user_quotas
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))
            
            users = []
            for row in cur.fetchall():
                users.append({
                    "user_id": row['user_id'],
                    "role": row['role'],
                    "max_sessions": row['max_sessions'],
                    "max_concurrent_sessions": row['max_concurrent_sessions'],
                    "max_daily_sessions": row['max_daily_sessions'],
                    "enabled": bool(row['enabled']),
                    "created_at": row['created_at'],
                    "updated_at": row['updated_at']
                })
            
            return users
    
    def cleanup_old_usage_data(self, max_age_days: int = 90) -> int:
        """
        Remove usage data older than specified days
        
        Args:
            max_age_days: Maximum age in days
        
        Returns:
            Number of records deleted
        """
        with self._get_cursor() as cur:
            cur.execute("""
                DELETE FROM usage_tracking
                WHERE timestamp < datetime('now', '-' || ? || ' days')
            """, (max_age_days,))
            deleted = cur.rowcount
        
        logger.info(f"Cleaned up {deleted} old usage records")
        return deleted


# Global quota manager instance
_quota_manager: Optional[QuotaManager] = None


def get_quota_manager() -> QuotaManager:
    """Get or create global quota manager instance"""
    global _quota_manager
    if _quota_manager is None:
        db_path = Path("/var/lib/sshbox/quotas.db")
        if not db_path.parent.exists():
            db_path = Path("/tmp/sshbox/quotas.db")
        
        # Try to get Redis client
        redis_client = None
        try:
            redis_client = redis.Redis(
                host=os.environ.get('REDIS_HOST', 'localhost'),
                port=int(os.environ.get('REDIS_PORT', 6379)),
                decode_responses=True
            )
            redis_client.ping()
        except Exception:
            pass
        
        _quota_manager = QuotaManager(
            db_path=str(db_path),
            redis_client=redis_client
        )
    return _quota_manager


# Import os for the global instance
import os
