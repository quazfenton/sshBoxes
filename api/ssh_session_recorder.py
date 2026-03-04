#!/usr/bin/env python3
"""
SSH Session Recorder for sshBox
Captures and records SSH sessions using asciinema and script commands
"""
import os
import json
import sqlite3
import subprocess
import threading
import time
import logging
import re
import signal
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict
import tempfile
import shutil

from api.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class SessionRecording:
    """Represents a session recording"""
    session_id: str
    container_name: str
    user_id: Optional[str]
    profile: str
    ttl: int
    start_time: str
    end_time: Optional[str] = None
    duration_seconds: Optional[float] = None
    recording_path: Optional[str] = None
    timing_path: Optional[str] = None
    metadata_path: Optional[str] = None
    invited_by: Optional[str] = None
    allowed_actions: Optional[List[str]] = None
    status: str = "active"
    commands_executed: Optional[List[Dict[str, str]]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SSHSessionRecorder:
    """
    Records SSH sessions with asciinema casting format
    Supports real-time streaming and playback
    """
    
    def __init__(
        self,
        db_path: Optional[str] = None,
        recordings_dir: Optional[str] = None
    ):
        settings = get_settings()
        
        self.db_path = Path(db_path or settings.database.sqlite_path)
        self.recordings_dir = Path(recordings_dir or settings.storage.recordings_dir)
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        
        # Ensure database directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Initializing SSHSessionRecorder with db_path: {self.db_path}, recordings_dir: {self.recordings_dir}")
        
        self._init_db()
    
    def _init_db(self):
        """Initialize the SQLite database with required tables"""
        logger.info(f"Initializing database at {self.db_path}")
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Create sessions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    container_name TEXT NOT NULL,
                    ssh_host TEXT,
                    ssh_port INTEGER,
                    ssh_user TEXT,
                    profile TEXT,
                    ttl INTEGER,
                    status TEXT DEFAULT 'active',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    started_at TEXT,
                    ended_at TEXT,
                    user_id TEXT,
                    invited_by TEXT,
                    allowed_actions TEXT,
                    recording_path TEXT,
                    timing_path TEXT
                )
            """)
            
            # Create session_recordings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS session_recordings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    recording_path TEXT,
                    recording_size INTEGER,
                    duration_seconds REAL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
                )
            """)
            
            # Create commands table for command auditing
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS session_commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    command TEXT NOT NULL,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    exit_code INTEGER,
                    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
                )
            """)
            
            # Create indexes for performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_recordings_session ON session_recordings(session_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_commands_session ON session_commands(session_id)")
            
            conn.commit()
        
        logger.info("Database initialized successfully")
    
    def start_recording(
        self,
        session_id: str,
        container_name: str,
        user_id: Optional[str] = None,
        profile: str = "dev",
        ttl: int = 1800,
        invited_by: Optional[str] = None,
        allowed_actions: Optional[List[str]] = None
    ) -> SessionRecording:
        """
        Start recording a session.
        
        This prepares the recording infrastructure but doesn't start the actual
        SSH session recording - that happens when the user connects.
        """
        logger.info(f"Starting recording for session {session_id}, container {container_name}")
        
        try:
            # Create recording file paths
            recording_file = self.recordings_dir / f"{session_id}.cast"
            timing_file = self.recordings_dir / f"{session_id}.timing"
            metadata_file = self.recordings_dir / f"{session_id}.meta.json"
            
            # Insert session record into database
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO sessions
                    (session_id, container_name, profile, ttl, status, created_at, user_id, invited_by, allowed_actions)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session_id,
                    container_name,
                    profile,
                    ttl,
                    'active',
                    datetime.utcnow().isoformat(),
                    user_id,
                    invited_by,
                    json.dumps(allowed_actions) if allowed_actions else None
                ))
                conn.commit()
            
            # Create initial metadata
            metadata = {
                "session_id": session_id,
                "container_name": container_name,
                "user_id": user_id,
                "profile": profile,
                "ttl": ttl,
                "start_time": datetime.utcnow().isoformat(),
                "recording_file": str(recording_file),
                "timing_file": str(timing_file),
                "invited_by": invited_by,
                "allowed_actions": allowed_actions,
                "version": 2,  # asciinema cast format version
                "idle_time_limit": None
            }
            
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            logger.info(f"Recording started successfully for session {session_id}")
            
            return SessionRecording(
                session_id=session_id,
                container_name=container_name,
                user_id=user_id,
                profile=profile,
                ttl=ttl,
                start_time=metadata["start_time"],
                recording_path=str(recording_file),
                timing_path=str(timing_file),
                metadata_path=str(metadata_file),
                invited_by=invited_by,
                allowed_actions=allowed_actions
            )
            
        except Exception as e:
            logger.error(f"Error starting recording for session {session_id}: {e}", exc_info=True)
            raise
    
    def wrap_ssh_command(
        self,
        session_id: str,
        ssh_command: List[str],
        output_file: Optional[str] = None
    ) -> Tuple[List[str], str]:
        """
        Wrap SSH command with recording using asciinema or script.
        
        Args:
            session_id: Session identifier
            ssh_command: Original SSH command list
            output_file: Optional output file for recording
        
        Returns:
            Tuple of (wrapped_command, recording_file_path)
        """
        if output_file is None:
            output_file = str(self.recordings_dir / f"{session_id}.cast")
        
        # Check if asciinema is available
        asciinema_available = shutil.which('asciinema') is not None
        
        if asciinema_available:
            # Use asciinema for rich recording with timing
            wrapped_command = [
                'asciinema',
                'rec',
                '--stdin',
                '--overwrite',
                '-c', ' '.join(ssh_command),
                output_file
            ]
            logger.info(f"Using asciinema for session recording: {session_id}")
        else:
            # Fall back to script command
            timing_file = str(self.recordings_dir / f"{session_id}.timing")
            wrapped_command = [
                'script',
                '--flush',
                '--timing', timing_file,
                '--command', ' '.join(ssh_command),
                output_file
            ]
            logger.info(f"Using script for session recording: {session_id}")
        
        return wrapped_command, output_file
    
    def record_command(
        self,
        session_id: str,
        command: str,
        exit_code: Optional[int] = None
    ):
        """Record a command executed during a session"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO session_commands
                    (session_id, command, exit_code, timestamp)
                    VALUES (?, ?, ?, ?)
                """, (
                    session_id,
                    command,
                    exit_code,
                    datetime.utcnow().isoformat()
                ))
                conn.commit()
        except Exception as e:
            logger.error(f"Error recording command for session {session_id}: {e}")
    
    def stop_recording(self, session_id: str) -> Optional[SessionRecording]:
        """Stop recording and finalize session data"""
        logger.info(f"Stopping recording for session {session_id}")
        
        try:
            # Update session status in database
            end_time = datetime.utcnow().isoformat()
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Get session info
                cursor.execute("""
                    SELECT created_at, recording_path FROM sessions
                    WHERE session_id = ?
                """, (session_id,))
                row = cursor.fetchone()
                
                if not row:
                    logger.warning(f"Session {session_id} not found")
                    return None
                
                created_at, recording_path = row
                
                # Update session status
                cursor.execute("""
                    UPDATE sessions
                    SET status = 'ended', ended_at = ?
                    WHERE session_id = ?
                """, (end_time, session_id))
                
                # Calculate duration
                try:
                    start = datetime.fromisoformat(created_at.replace('Z', '').split('.')[0])
                    end = datetime.fromisoformat(end_time.replace('Z', '').split('.')[0])
                    duration = (end - start).total_seconds()
                except Exception:
                    duration = 0
                
                # Add to recordings table
                recording_size = 0
                if recording_path and Path(recording_path).exists():
                    recording_size = Path(recording_path).stat().st_size
                    
                    cursor.execute("""
                        INSERT INTO session_recordings
                        (session_id, recording_path, recording_size, duration_seconds, created_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        session_id,
                        recording_path,
                        recording_size,
                        duration,
                        end_time
                    ))
                
                conn.commit()
            
            # Update metadata file
            metadata_file = self.recordings_dir / f"{session_id}.meta.json"
            if metadata_file.exists():
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                
                metadata["end_time"] = end_time
                metadata["duration"] = duration
                
                with open(metadata_file, 'w') as f:
                    json.dump(metadata, f, indent=2)
            
            logger.info(f"Recording stopped successfully for session {session_id}, duration: {duration}s")
            
            return self.get_recording(session_id)
            
        except Exception as e:
            logger.error(f"Error stopping recording for session {session_id}: {e}", exc_info=True)
            return None
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve session information from database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
                row = cursor.fetchone()
                
                if not row:
                    return None
                
                columns = [desc[0] for desc in cursor.description]
                session = dict(zip(columns, row))
                
                # Parse allowed_actions if it exists
                if session.get('allowed_actions'):
                    session['allowed_actions'] = json.loads(session['allowed_actions'])
                
                return session
        except Exception as e:
            logger.error(f"Error getting session {session_id}: {e}")
            return None
    
    def get_recording(self, session_id: str) -> Optional[SessionRecording]:
        """Retrieve recording metadata"""
        session_info = self.get_session(session_id)
        if not session_info:
            return None
        
        # Load metadata file if exists
        metadata_file = self.recordings_dir / f"{session_id}.meta.json"
        metadata = {}
        if metadata_file.exists():
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
        
        return SessionRecording(
            session_id=session_info['session_id'],
            container_name=session_info['container_name'],
            user_id=session_info.get('user_id'),
            profile=session_info['profile'],
            ttl=session_info['ttl'],
            start_time=session_info['created_at'],
            end_time=session_info.get('ended_at'),
            recording_path=session_info.get('recording_path'),
            invited_by=session_info.get('invited_by'),
            allowed_actions=session_info.get('allowed_actions'),
            status=session_info['status']
        )
    
    def get_recording_content(self, session_id: str) -> Optional[str]:
        """Retrieve recording content (asciicast format)"""
        session_info = self.get_session(session_id)
        if not session_info:
            return None
        
        recording_path = session_info.get('recording_path')
        if not recording_path or not Path(recording_path).exists():
            return None
        
        # Validate path is within recordings directory
        abs_recording = Path(recording_path).resolve()
        if not str(abs_recording).startswith(str(self.recordings_dir.resolve())):
            logger.warning(f"Recording path traversal attempt: {recording_path}")
            return None
        
        try:
            with open(recording_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error reading recording {session_id}: {e}")
            return None
    
    def list_recordings(
        self,
        limit: int = 100,
        status: Optional[str] = None,
        profile: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List recordings with optional filters"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                query = """
                    SELECT s.*, r.recording_path, r.recording_size, r.duration_seconds
                    FROM sessions s
                    LEFT JOIN session_recordings r ON s.session_id = r.session_id
                    WHERE 1=1
                """
                params = []
                
                if status:
                    query += " AND s.status = ?"
                    params.append(status)
                
                if profile:
                    query += " AND s.profile = ?"
                    params.append(profile)
                
                query += " ORDER BY s.created_at DESC LIMIT ?"
                params.append(limit)
                
                cursor.execute(query, params)
                rows = cursor.fetchall()
                
                columns = [desc[0] for desc in cursor.description]
                recordings = []
                
                for row in rows:
                    recording = dict(zip(columns, row))
                    if recording.get('allowed_actions'):
                        recording['allowed_actions'] = json.loads(recording['allowed_actions'])
                    recordings.append(recording)
                
                return recordings
                
        except Exception as e:
            logger.error(f"Error listing recordings: {e}")
            return []
    
    def get_commands(self, session_id: str, limit: int = 1000) -> List[Dict[str, Any]]:
        """Get commands executed during a session"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM session_commands
                    WHERE session_id = ?
                    ORDER BY timestamp ASC
                    LIMIT ?
                """, (session_id, limit))
                
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description]
                
                return [dict(zip(columns, row)) for row in rows]
                
        except Exception as e:
            logger.error(f"Error getting commands for session {session_id}: {e}")
            return []
    
    def cleanup_old_recordings(self, days: int = 7) -> int:
        """Remove recordings older than specified days"""
        logger.info(f"Cleaning up recordings older than {days} days")
        
        cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cleaned_count = 0
        
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Find old sessions to clean up
                cursor.execute("""
                    SELECT session_id, recording_path FROM sessions
                    WHERE created_at < ?
                    AND (status = 'ended' OR status = 'destroyed')
                """, (cutoff_date,))
                
                old_sessions = cursor.fetchall()
                
                # Remove associated files
                for session_id, recording_path in old_sessions:
                    # Remove recording files
                    for ext in ['.cast', '.timing', '.meta.json']:
                        file_path = self.recordings_dir / f"{session_id}{ext}"
                        if file_path.exists():
                            try:
                                file_path.unlink()
                                logger.debug(f"Removed file: {file_path}")
                            except Exception as e:
                                logger.warning(f"Failed to remove file {file_path}: {e}")
                    
                    # Remove rootfs if exists (Firecracker)
                    session_dir = Path(f"/tmp/firecracker-session-{session_id}")
                    if session_dir.exists():
                        try:
                            shutil.rmtree(session_dir)
                        except Exception as e:
                            logger.warning(f"Failed to remove session dir {session_dir}: {e}")
                    
                    cleaned_count += 1
                
                # Remove from database
                cursor.execute("""
                    DELETE FROM session_commands
                    WHERE session_id IN (
                        SELECT session_id FROM sessions
                        WHERE created_at < ?
                        AND (status = 'ended' OR status = 'destroyed')
                    )
                """, (cutoff_date,))
                
                cursor.execute("""
                    DELETE FROM session_recordings
                    WHERE session_id IN (
                        SELECT session_id FROM sessions
                        WHERE created_at < ?
                        AND (status = 'ended' OR status = 'destroyed')
                    )
                """, (cutoff_date,))
                
                cursor.execute("""
                    DELETE FROM sessions
                    WHERE created_at < ?
                    AND (status = 'ended' OR status = 'destroyed')
                """, (cutoff_date,))
                
                conn.commit()
            
            logger.info(f"Cleaned up {cleaned_count} old recordings")
            return cleaned_count
            
        except Exception as e:
            logger.error(f"Error cleaning up recordings: {e}")
            return 0


# Import timedelta for cleanup
from datetime import timedelta


# FastAPI integration
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List

app = FastAPI(title="SSH Session Recorder API")

# Global recorder instance
recorder = SSHSessionRecorder(
    db_path=get_settings().database.sqlite_path,
    recordings_dir=get_settings().storage.recordings_dir
)


class RecordingRequest(BaseModel):
    session_id: str
    container_name: str
    user_id: Optional[str] = None
    profile: str = "dev"
    ttl: int = 1800
    invited_by: Optional[str] = None
    allowed_actions: Optional[List[str]] = None


class CommandRecord(BaseModel):
    session_id: str
    command: str
    exit_code: Optional[int] = None


@app.post("/recordings/start")
async def start_recording(req: RecordingRequest):
    """Start a new session recording"""
    try:
        result = recorder.start_recording(
            session_id=req.session_id,
            container_name=req.container_name,
            user_id=req.user_id,
            profile=req.profile,
            ttl=req.ttl,
            invited_by=req.invited_by,
            allowed_actions=req.allowed_actions
        )
        return result.to_dict()
    except Exception as e:
        logger.error(f"Error starting recording: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/recordings/stop/{session_id}")
async def stop_recording(session_id: str):
    """Stop a session recording"""
    try:
        result = recorder.stop_recording(session_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return result.to_dict()
    except Exception as e:
        logger.error(f"Error stopping recording: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/recordings/{session_id}")
async def get_recording(session_id: str):
    """Get a specific recording"""
    try:
        result = recorder.get_recording(session_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        return result.to_dict()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/recordings/{session_id}/content")
async def get_recording_content(session_id: str):
    """Get recording content (asciicast format)"""
    try:
        content = recorder.get_recording_content(session_id)
        if content is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(content, media_type="text/plain")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/recordings/{session_id}/commands")
async def get_commands(session_id: str, limit: int = Query(default=1000, le=10000)):
    """Get commands executed during a session"""
    try:
        commands = recorder.get_commands(session_id, limit)
        return commands
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/recordings")
async def list_recordings(
    limit: int = Query(default=50, le=500),
    status: Optional[str] = None,
    profile: Optional[str] = None
):
    """List all recordings"""
    try:
        return recorder.list_recordings(limit=limit, status=status, profile=profile)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cleanup")
async def cleanup_recordings(days: int = Query(default=7, ge=1)):
    """Clean up old recordings"""
    try:
        count = recorder.cleanup_old_recordings(days=days)
        return {"cleaned": count, "days": days}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Health check"""
    return {
        "status": "healthy",
        "db_path": str(recorder.db_path),
        "recordings_dir": str(recorder.recordings_dir)
    }


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get('RECORDER_PORT', 8082)),
        log_level="info"
    )
