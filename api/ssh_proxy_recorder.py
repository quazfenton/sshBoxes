#!/usr/bin/env python3
"""
SSH Proxy Recorder for sshBox
Captures and records SSH sessions using asciinema or script command
Provides real-time streaming and playback capabilities
"""
import os
import sys
import pty
import select
import socket
import signal
import struct
import fcntl
import termios
import logging
import subprocess
import threading
import time
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass, asdict
import queue
import tempfile

from api.exceptions import RecordingError, ConfigurationError
from api.logging_config import setup_logging
from api.session_recorder import SessionRecorder, is_safe_path

logger = setup_logging("ssh_proxy_recorder")


@dataclass
class RecordingSession:
    """Recording session metadata"""
    session_id: str
    user_id: str
    ssh_host: str
    ssh_port: int
    ssh_user: str
    start_time: str
    recording_path: str
    cast_path: str
    timing_path: str
    status: str = "recording"
    duration_seconds: float = 0
    bytes_recorded: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AsciinemaRecorder:
    """
    Record SSH sessions using asciinema
    """
    
    def __init__(self, recordings_dir: str = "/tmp/sshbox_recordings"):
        self.recordings_dir = Path(recordings_dir)
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        
        # Check if asciinema is available
        self.asciinema_available = self._check_asciinema()
        
        if not self.asciinema_available:
            logger.warning("asciinema not available, falling back to script command")
    
    def _check_asciinema(self) -> bool:
        """Check if asciinema is installed"""
        try:
            result = subprocess.run(
                ["asciinema", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def start_recording(
        self,
        session_id: str,
        ssh_host: str,
        ssh_port: int,
        ssh_user: str,
        private_key_path: str,
        user_id: str = None,
        extra_env: Dict = None
    ) -> Tuple[RecordingSession, subprocess.Popen]:
        """
        Start recording an SSH session
        
        Args:
            session_id: Session identifier
            ssh_host: SSH host
            ssh_port: SSH port
            ssh_user: SSH user
            private_key_path: Path to private key
            user_id: User identifier
            extra_env: Extra environment variables
        
        Returns:
            Tuple of (RecordingSession, subprocess.Popen)
        """
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        cast_path = self.recordings_dir / f"{session_id}_{timestamp}.cast"
        timing_path = self.recordings_dir / f"{session_id}_{timestamp}.timing"
        
        # Build SSH command
        ssh_cmd = [
            "ssh",
            "-i", private_key_path,
            "-p", str(ssh_port),
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            "-tt",
            f"{ssh_user}@{ssh_host}"
        ]
        
        # Build asciinema command
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        
        if self.asciinema_available:
            # Use asciinema for recording
            rec_cmd = [
                "asciinema",
                "rec",
                "--command", " ".join(ssh_cmd),
                str(cast_path),
                "--title", f"sshBox Session: {session_id}",
                "--overwrite"
            ]
        else:
            # Fallback to script command
            rec_cmd = [
                "script",
                "-f", "-c", " ".join(ssh_cmd),
                str(cast_path)
            ]
        
        logger.info(f"Starting recording for session {session_id}")
        
        # Start recording process
        try:
            process = subprocess.Popen(
                rec_cmd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            recording_session = RecordingSession(
                session_id=session_id,
                user_id=user_id or "unknown",
                ssh_host=ssh_host,
                ssh_port=ssh_port,
                ssh_user=ssh_user,
                start_time=datetime.utcnow().isoformat(),
                recording_path=str(cast_path),
                cast_path=str(cast_path),
                timing_path=str(timing_path),
                status="recording"
            )
            
            return recording_session, process
            
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            raise RecordingError(
                reason=f"Failed to start recording: {e}",
                session_id=session_id
            )
    
    def stop_recording(
        self,
        session: RecordingSession,
        process: subprocess.Popen,
        timeout: int = 10
    ) -> RecordingSession:
        """
        Stop recording session
        
        Args:
            session: Recording session
            process: Recording process
            timeout: Timeout for graceful shutdown
        
        Returns:
            Updated RecordingSession
        """
        try:
            # Send EOF to stdin
            if process.stdin:
                process.stdin.close()
            
            # Wait for process to finish
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning(f"Recording process didn't exit gracefully, terminating")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            
            # Update session metadata
            session.status = "completed"
            session.end_time = datetime.utcnow().isoformat()
            
            start = datetime.fromisoformat(session.start_time.replace('Z', '+00:00'))
            end = datetime.fromisoformat(session.end_time.replace('Z', '+00:00'))
            session.duration_seconds = (end - start).total_seconds()
            
            # Get file size
            cast_path = Path(session.cast_path)
            if cast_path.exists():
                session.bytes_recorded = cast_path.stat().st_size
            
            logger.info(
                f"Recording completed: session={session.session_id}, "
                f"duration={session.duration_seconds:.1f}s, "
                f"bytes={session.bytes_recorded}"
            )
            
            return session
            
        except Exception as e:
            logger.error(f"Error stopping recording: {e}")
            session.status = "error"
            return session


class SSHTTYProxy:
    """
    SSH Proxy that provides interactive terminal with recording
    
    This proxy sits between the client and SSH server, recording
    all input and output while providing full terminal functionality.
    """
    
    def __init__(
        self,
        ssh_host: str,
        ssh_port: int,
        ssh_user: str,
        private_key_path: str,
        session_id: str,
        recordings_dir: str = "/tmp/sshbox_recordings"
    ):
        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.ssh_user = ssh_user
        self.private_key_path = private_key_path
        self.session_id = session_id
        self.recordings_dir = Path(recordings_dir)
        
        self.master_fd = None
        self.pid = None
        self.recording_file = None
        self.running = False
        self._lock = threading.Lock()
    
    def _setup_pty(self) -> Tuple[int, int]:
        """Create pseudo-terminal"""
        master, slave = pty.openpty()
        
        # Set terminal size
        winsize = struct.pack('HHHH', 24, 80, 0, 0)
        fcntl.ioctl(master, termios.TIOCSWINSZ, winsize)
        
        return master, slave
    
    def _fork_and_exec(self, slave_fd: int) -> int:
        """Fork process and execute SSH"""
        pid = os.fork()
        
        if pid == 0:
            # Child process
            os.close(slave_fd)
            
            # Set up session
            os.setsid()
            
            # Duplicate slave to stdin, stdout, stderr
            os.dup2(0, slave_fd)
            os.dup2(1, slave_fd)
            os.dup2(2, slave_fd)
            
            # Execute SSH
            ssh_cmd = [
                "ssh",
                "-i", self.private_key_path,
                "-p", str(self.ssh_port),
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR",
                "-tt",
                f"{self.ssh_user}@{self.ssh_host}"
            ]
            
            os.execvp("ssh", ssh_cmd)
        else:
            # Parent process
            os.close(slave_fd)
        
        return pid
    
    def _record_io(self, master_fd: int, output_file: str):
        """Record terminal I/O to file"""
        self.running = True
        start_time = time.time()
        bytes_written = 0
        
        try:
            with open(output_file, 'wb') as f:
                # Write asciinema header
                header = {
                    "version": 2,
                    "width": 80,
                    "height": 24,
                    "timestamp": int(start_time),
                    "env": {"shell": "/bin/bash"}
                }
                f.write(json.dumps(header).encode() + b'\n')
                
                while self.running:
                    try:
                        ready, _, _ = select.select([master_fd], [], [], 0.1)
                        
                        if ready:
                            try:
                                data = os.read(master_fd, 4096)
                                if not data:
                                    break
                                
                                # Write timing data (asciinema format)
                                elapsed = time.time() - start_time
                                timing_entry = [elapsed, 'o', data.decode('utf-8', errors='replace')]
                                f.write(json.dumps(timing_entry).encode() + b'\n')
                                bytes_written += len(data)
                                
                                # Write to stdout
                                sys.stdout.buffer.write(data)
                                sys.stdout.buffer.flush()
                                
                            except OSError:
                                break
                    except KeyboardInterrupt:
                        break
                
        except Exception as e:
            logger.error(f"Recording error: {e}")
        finally:
            self.running = False
    
    def start(self) -> bool:
        """Start the SSH proxy with recording"""
        try:
            # Create PTY
            master_fd, slave_fd = self._setup_pty()
            
            # Fork and exec SSH
            pid = self._fork_and_exec(slave_fd)
            
            self.master_fd = master_fd
            self.pid = pid
            
            # Start recording thread
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            output_file = self.recordings_dir / f"{self.session_id}_{timestamp}.cast"
            self.recording_file = str(output_file)
            
            record_thread = threading.Thread(
                target=self._record_io,
                args=(master_fd, str(output_file)),
                daemon=True
            )
            record_thread.start()
            
            logger.info(f"SSH proxy started: pid={pid}, recording={output_file}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start SSH proxy: {e}")
            return False
    
    def stop(self, timeout: int = 10) -> Dict[str, Any]:
        """Stop the SSH proxy"""
        self.running = False
        
        # Close master FD
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
        
        # Wait for child process
        if self.pid is not None:
            try:
                os.waitpid(self.pid, 0)
            except OSError:
                pass
        
        return {
            "session_id": self.session_id,
            "recording_file": self.recording_file,
            "status": "completed"
        }
    
    def resize_terminal(self, width: int, height: int):
        """Resize terminal"""
        if self.master_fd is not None:
            winsize = struct.pack('HHHH', height, width, 0, 0)
            try:
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass


class SSHProxyService:
    """
    Service for managing SSH proxy recordings
    
    Usage:
        service = SSHProxyService()
        
        # Start a recorded session
        session = service.start_session(
            session_id="box_123",
            ssh_host="192.168.1.100",
            ssh_port=2222,
            ssh_user="boxuser",
            private_key_path="/tmp/proxy_key",
            user_id="user@example.com"
        )
        
        # Get session status
        status = service.get_session_status("box_123")
        
        # Stop session
        service.stop_session("box_123")
    """
    
    def __init__(
        self,
        recordings_dir: str = "/tmp/sshbox_recordings",
        max_concurrent_recordings: int = 100,
        retention_days: int = 7
    ):
        self.recordings_dir = Path(recordings_dir)
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        
        self.max_concurrent = max_concurrent_recordings
        self.retention_days = retention_days
        
        self.recorder = AsciinemaRecorder(str(recordings_dir))
        self._sessions: Dict[str, RecordingSession] = {}
        self._processes: Dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()
        
        # Generate proxy key for SSH connections
        self._proxy_key_path = self._generate_proxy_key()
    
    def _generate_proxy_key(self) -> str:
        """Generate SSH key for proxy connections"""
        key_path = self.recordings_dir / ".proxy_key"
        
        if not key_path.exists():
            try:
                subprocess.run(
                    ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", ""],
                    capture_output=True,
                    check=True
                )
                os.chmod(key_path, 0o600)
                logger.info("Generated proxy SSH key")
            except Exception as e:
                logger.error(f"Failed to generate proxy key: {e}")
        
        return str(key_path)
    
    def start_session(
        self,
        session_id: str,
        ssh_host: str,
        ssh_port: int,
        ssh_user: str,
        user_id: str = None,
        private_key_path: str = None
    ) -> RecordingSession:
        """
        Start a recorded SSH session
        
        Args:
            session_id: Session identifier
            ssh_host: SSH host
            ssh_port: SSH port
            ssh_user: SSH user
            user_id: User identifier
            private_key_path: Optional private key (uses proxy key if not provided)
        
        Returns:
            RecordingSession metadata
        """
        with self._lock:
            # Check concurrent limit
            if len(self._sessions) >= self.max_concurrent:
                raise RecordingError(
                    reason=f"Maximum concurrent recordings ({self.max_concurrent}) reached",
                    session_id=session_id
                )
            
            # Check if session already exists
            if session_id in self._sessions:
                raise RecordingError(
                    reason=f"Session {session_id} already exists",
                    session_id=session_id
                )
        
        # Use provided key or proxy key
        key_path = private_key_path or self._proxy_key_path
        
        # Start recording
        session, process = self.recorder.start_recording(
            session_id=session_id,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_user=ssh_user,
            private_key_path=key_path,
            user_id=user_id
        )
        
        with self._lock:
            self._sessions[session_id] = session
            self._processes[session_id] = process
        
        logger.info(f"Started recording session {session_id}")
        return session
    
    def stop_session(self, session_id: str, timeout: int = 10) -> RecordingSession:
        """
        Stop a recording session
        
        Args:
            session_id: Session identifier
            timeout: Timeout for graceful shutdown
        
        Returns:
            Updated RecordingSession
        """
        with self._lock:
            if session_id not in self._sessions:
                raise RecordingError(
                    reason=f"Session {session_id} not found",
                    session_id=session_id
                )
            
            session = self._sessions[session_id]
            process = self._processes.get(session_id)
        
        if process:
            session = self.recorder.stop_recording(session, process, timeout)
        
        with self._lock:
            self._sessions.pop(session_id, None)
            self._processes.pop(session_id, None)
        
        logger.info(f"Stopped recording session {session_id}")
        return session
    
    def get_session_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session status"""
        with self._lock:
            session = self._sessions.get(session_id)
        
        if session:
            return session.to_dict()
        return None
    
    def list_sessions(self, status: str = None) -> List[Dict[str, Any]]:
        """List all sessions"""
        with self._lock:
            sessions = list(self._sessions.values())
        
        if status:
            sessions = [s for s in sessions if s.status == status]
        
        return [s.to_dict() for s in sessions]
    
    def get_recording_content(self, session_id: str) -> Optional[str]:
        """Get recording content"""
        with self._lock:
            session = self._sessions.get(session_id)
        
        if not session:
            return None
        
        cast_path = Path(session.cast_path)
        if cast_path.exists():
            try:
                with open(cast_path, 'r') as f:
                    return f.read()
            except Exception as e:
                logger.error(f"Error reading recording: {e}")
        
        return None
    
    def cleanup_old_recordings(self, max_age_days: int = None) -> int:
        """Clean up old recordings"""
        if max_age_days is None:
            max_age_days = self.retention_days
        
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        deleted = 0
        
        for cast_file in self.recordings_dir.glob("*.cast"):
            try:
                mtime = datetime.fromtimestamp(cast_file.stat().st_mtime)
                if mtime < cutoff:
                    cast_file.unlink()
                    
                    # Also remove related files
                    session_id = cast_file.stem
                    for ext in ['.timing', '.json']:
                        related = cast_file.with_suffix(ext)
                        if related.exists():
                            related.unlink()
                    
                    deleted += 1
            except Exception as e:
                logger.warning(f"Error cleaning up {cast_file}: {e}")
        
        logger.info(f"Cleaned up {deleted} old recordings")
        return deleted


# Global service instance
_proxy_service: Optional[SSHProxyService] = None


def get_proxy_service() -> SSHProxyService:
    """Get or create global proxy service"""
    global _proxy_service
    if _proxy_service is None:
        recordings_dir = os.environ.get('RECORDINGS_DIR', '/tmp/sshbox_recordings')
        _proxy_service = SSHProxyService(recordings_dir=recordings_dir)
    return _proxy_service


# Import timedelta for cleanup
from datetime import timedelta
