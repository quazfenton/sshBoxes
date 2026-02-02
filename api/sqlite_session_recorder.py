#!/usr/bin/env python3
"""
SQLite-based Session Recording Module for sshBox
Records SSH sessions and stores metadata in SQLite
"""
import os
import json
import sqlite3
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import subprocess
import threading
from logging.handlers import RotatingFileHandler


# Set up logging
logs_dir = os.environ.get('LOGS_DIR', '/tmp/sshbox_logs')
os.makedirs(logs_dir, exist_ok=True)

logger = logging.getLogger("recorder")
logger.setLevel(logging.INFO)

# Create formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# File handler with rotation
log_file = os.path.join(logs_dir, "recorder.log")
file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


class SQLiteSessionRecorder:
    def __init__(self, db_path: str = "/tmp/sshbox_sessions.db", recordings_dir: str = "/tmp/sshbox_recordings"):
        self.db_path = Path(db_path)
        self.recordings_dir = Path(recordings_dir)
        self.recordings_dir.mkdir(exist_ok=True)

        logger.info(f"Initializing SQLiteSessionRecorder with db_path: {db_path}, recordings_dir: {recordings_dir}")

        # Initialize the database
        self._init_db()
    
    def _init_db(self):
        """Initialize the SQLite database with required tables"""
        logger.info(f"Initializing database at {self.db_path}")
        try:
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
                        allowed_actions TEXT
                    )
                """)

                # Create session_recordings table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS session_recordings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT,
                        recording_path TEXT,
                        recording_size INTEGER,
                        duration_seconds INTEGER,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (session_id) REFERENCES sessions (session_id)
                    )
                """)

                # Create invites table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS invites (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        token TEXT UNIQUE NOT NULL,
                        profile TEXT,
                        ttl INTEGER,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        used_at TEXT,
                        created_by TEXT,
                        status TEXT DEFAULT 'valid'
                    )
                """)

                conn.commit()
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            raise
    
    def start_recording(self, session_id: str, container_name: str, user_id: str = None,
                       profile: str = "dev", ttl: int = 1800, invited_by: str = None,
                       allowed_actions: List[str] = None):
        """Start recording a session and store metadata in SQLite"""
        logger.info(f"Starting recording for session {session_id}, container {container_name}, profile {profile}")

        try:
            # Create recording files
            recording_file = self.recordings_dir / f"{session_id}.cast"
            timing_file = self.recordings_dir / f"{session_id}.timing"

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

            # Create metadata for the recording
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
                "allowed_actions": allowed_actions
            }

            metadata_file = self.recordings_dir / f"{session_id}.json"
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)

            logger.info(f"Recording started successfully for session {session_id}")

            return {
                "recording_file": str(recording_file),
                "timing_file": str(timing_file),
                "metadata_file": str(metadata_file),
                "session_id": session_id
            }
        except Exception as e:
            logger.error(f"Error starting recording for session {session_id}: {e}")
            raise
    
    def stop_recording(self, session_id: str):
        """Stop recording and update session status in database"""
        # Update session status to 'ended'
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE sessions 
                SET status = 'ended', ended_at = ?
                WHERE session_id = ?
            """, (datetime.utcnow().isoformat(), session_id))
            conn.commit()
        
        # Update recording metadata
        metadata_file = self.recordings_dir / f"{session_id}.json"
        if metadata_file.exists():
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
            
            metadata["end_time"] = datetime.utcnow().isoformat()
            metadata["duration"] = (
                datetime.fromisoformat(metadata["end_time"]) -
                datetime.fromisoformat(metadata["start_time"])
            ).total_seconds()
            
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            # Add to recordings table
            recording_file = Path(metadata["recording_file"])
            if recording_file.exists():
                recording_size = recording_file.stat().st_size
                
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO session_recordings 
                        (session_id, recording_path, recording_size, duration_seconds, created_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        session_id,
                        str(recording_file),
                        recording_size,
                        metadata["duration"],
                        datetime.utcnow().isoformat()
                    ))
                    conn.commit()
        
        return self.get_session(session_id)
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve session information from database"""
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
    
    def get_recording(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve recording metadata and content"""
        # Get session info
        session_info = self.get_session(session_id)
        if not session_info:
            return None
        
        # Add recording information
        metadata_file = self.recordings_dir / f"{session_id}.json"
        if metadata_file.exists():
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
            session_info.update(metadata)
        
        # Get recording file if it exists
        recording_file = self.recordings_dir / f"{session_id}.cast"
        if recording_file.exists():
            with open(recording_file, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            session_info["content"] = content
        
        return session_info
    
    def list_recordings(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List all available recordings"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.*, r.recording_path, r.duration_seconds, r.created_at as recording_created_at
                FROM sessions s
                LEFT JOIN session_recordings r ON s.session_id = r.session_id
                ORDER BY s.created_at DESC
                LIMIT ?
            """, (limit,))
            
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            
            recordings = []
            for row in rows:
                recording = dict(zip(columns, row))
                # Parse allowed_actions if it exists
                if recording.get('allowed_actions'):
                    recording['allowed_actions'] = json.loads(recording['allowed_actions'])
                recordings.append(recording)
        
        return recordings
    
    def cleanup_old_recordings(self, days: int = 7):
        """Remove recordings older than specified days"""
        cutoff_date = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        
        # Find old sessions to clean up
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT session_id FROM sessions
                WHERE created_at < datetime('now', '-' || ? || ' days')
                AND (status = 'ended' OR status = 'destroyed')
            """, (days,))
            
            old_sessions = cursor.fetchall()
        
        # Remove associated files
        for (session_id,) in old_sessions:
            for ext in ['.cast', '.timing', '.json']:
                file_path = self.recordings_dir / f"{session_id}{ext}"
                if file_path.exists():
                    file_path.unlink()
        
        # Remove from database
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM session_recordings 
                WHERE session_id IN (
                    SELECT session_id FROM sessions 
                    WHERE created_at < datetime('now', '-{} days')
                    AND (status = 'ended' OR status = 'destroyed')
                )
            """.format(days))
            
            cursor.execute("""
                DELETE FROM sessions 
                WHERE created_at < datetime('now', '-{} days')
                AND (status = 'ended' OR status = 'destroyed')
            """.format(days))
            
            conn.commit()
        
        print(f"Cleaned up {len(old_sessions)} old recordings")


# FastAPI integration for the recorder
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List

app = FastAPI(title="SSH Session Recorder API", description="API for managing SSH session recordings")

# Global recorder instance
recorder = SQLiteSessionRecorder(
    db_path=os.environ.get('RECORDER_DB_PATH', '/tmp/sshbox_sessions.db'),
    recordings_dir=os.environ.get('RECORDINGS_DIR', '/tmp/sshbox_recordings')
)

class RecordingRequest(BaseModel):
    session_id: str
    container_name: str
    user_id: Optional[str] = None
    profile: str = "dev"
    ttl: int = 1800
    invited_by: Optional[str] = None
    allowed_actions: Optional[List[str]] = []

class StopRecordingRequest(BaseModel):
    session_id: str

@app.post("/recordings/start", summary="Start a new session recording")
async def start_recording(req: RecordingRequest):
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
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/recordings/stop", summary="Stop a session recording")
async def stop_recording(req: StopRecordingRequest):
    try:
        result = recorder.stop_recording(req.session_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/recordings/{session_id}", summary="Get a specific recording")
async def get_recording(session_id: str):
    try:
        result = recorder.get_recording(session_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/recordings", summary="List all recordings")
async def list_recordings(limit: int = 50):
    try:
        return recorder.list_recordings(limit=limit)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health", summary="Health check")
async def health_check():
    return {"status": "healthy", "db_path": str(recorder.db_path)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get('RECORDER_PORT', 8082)),
        log_level="info"
    )