#!/usr/bin/env python3
"""
WebSocket Bridge for sshBox Web Terminal
Bridges WebSocket connections to SSH sessions

Note: Requires Python 3.10+ for asyncio.TaskGroup
For Python 3.8-3.9, use asyncio.gather instead
"""
import os
import sys
import json
import asyncio
import logging
import signal
import tempfile
from typing import Dict, Optional, Any
from pathlib import Path
import subprocess
import pty
import select
import struct
import fcntl
import termios

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.logging_config import setup_logging
from api.session_recorder import SessionRecorder

logger = setup_logging("websocket_bridge")

app = FastAPI(title="sshBox WebSocket Bridge")

# Add CORS middleware for web terminal
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files - path is relative to this file's directory
web_static_path = Path(__file__).parent / "static"
if web_static_path.exists():
    app.mount("/static", StaticFiles(directory=str(web_static_path)), name="static")

# Session storage
active_sessions: Dict[str, dict] = {}

# Gateway URL for session management
GATEWAY_URL = os.environ.get('GATEWAY_URL', 'http://localhost:8080')


class SSHSession:
    """Manages an SSH session with PTY"""
    
    def __init__(
        self,
        session_id: str,
        host: str,
        port: int,
        user: str,
        private_key: str,
        width: int = 80,
        height: int = 24
    ):
        self.session_id = session_id
        self.host = host
        self.port = port
        self.user = user
        self.private_key = private_key
        self.width = width
        self.height = height
        
        self.master_fd = None
        self.pid = None
        self.running = False
        self._lock = asyncio.Lock()
    
    def _create_pty(self) -> tuple:
        """Create pseudo-terminal"""
        master, slave = pty.openpty()
        
        # Set terminal size
        winsize = struct.pack('HHHH', self.height, self.width, 0, 0)
        fcntl.ioctl(master, termios.TIOCSWINSZ, winsize)
        
        return master, slave
    
    def _fork_ssh(self, slave_fd: int) -> int:
        """Fork and exec SSH"""
        pid = os.fork()
        
        if pid == 0:
            # Child process
            os.close(slave_fd)
            
            # New session
            os.setsid()
            
            # Duplicate slave to stdin, stdout, stderr
            os.dup2(0, slave_fd)
            os.dup2(1, slave_fd)
            os.dup2(2, slave_fd)
            
            # SSH command
            ssh_cmd = [
                "ssh",
                "-i", self.private_key,
                "-p", str(self.port),
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "LogLevel=ERROR",
                "-tt",
                f"{self.user}@{self.host}"
            ]
            
            os.execvp("ssh", ssh_cmd)
        else:
            # Parent process
            os.close(slave_fd)
        
        return pid
    
    async def start(self) -> bool:
        """Start SSH session"""
        try:
            # Create PTY
            master_fd, slave_fd = self._create_pty()
            
            # Fork SSH
            pid = self._fork_ssh(slave_fd)
            
            self.master_fd = master_fd
            self.pid = pid
            self.running = True
            
            logger.info(f"SSH session started: pid={pid}, session={self.session_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start SSH session: {e}")
            return False
    
    def stop(self):
        """Stop SSH session"""
        self.running = False
        
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
        
        if self.pid is not None:
            try:
                os.kill(self.pid, signal.SIGTERM)
                os.waitpid(self.pid, 0)
            except (OSError, ChildProcessError):
                pass
        
        logger.info(f"SSH session stopped: {self.session_id}")
    
    def resize(self, width: int, height: int):
        """Resize terminal"""
        if self.master_fd is not None:
            winsize = struct.pack('HHHH', height, width, 0, 0)
            try:
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass
    
    def write(self, data: bytes):
        """Write to PTY"""
        if self.master_fd is not None and self.running:
            try:
                os.write(self.master_fd, data)
            except OSError:
                pass
    
    def read(self, size: int = 4096) -> Optional[bytes]:
        """Read from PTY"""
        if self.master_fd is not None and self.running:
            try:
                ready, _, _ = select.select([self.master_fd], [], [], 0.1)
                if ready:
                    return os.read(self.master_fd, size)
            except OSError:
                pass
        return None


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "sshBox WebSocket Bridge",
        "status": "running",
        "active_sessions": len(active_sessions)
    }


@app.get("/health")
async def health():
    """Health check"""
    return {
        "status": "healthy",
        "active_sessions": len(active_sessions)
    }


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for terminal access"""
    await websocket.accept()
    
    ssh_session = None
    recorder = None
    
    try:
        # Get session info from gateway
        session_info = await get_session_info(session_id)
        
        if not session_info:
            await websocket.send_json({
                "type": "error",
                "message": "Session not found"
            })
            await websocket.close()
            return
        
        # Create temporary key file
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.key') as f:
            f.write(session_info.get('private_key', ''))
            key_path = f.name
        
        os.chmod(key_path, 0o600)
        
        # Create SSH session
        ssh_session = SSHSession(
            session_id=session_id,
            host=session_info['host'],
            port=session_info['port'],
            user=session_info['user'],
            private_key=key_path
        )
        
        # Start SSH session
        if not await ssh_session.start():
            await websocket.send_json({
                "type": "error",
                "message": "Failed to start SSH session"
            })
            await websocket.close()
            return
        
        # Start recording
        recorder = SessionRecorder()
        try:
            recorder.start_recording(
                session_id=session_id,
                user_id=session_info.get('user_id', 'unknown'),
                profile=session_info.get('profile', 'dev'),
                ttl=session_info.get('ttl', 1800)
            )
        except Exception as e:
            logger.warning(f"Failed to start recording: {e}")
        
        # Store session
        active_sessions[session_id] = {
            "ssh": ssh_session,
            "websocket": websocket,
            "started_at": asyncio.get_event_loop().time()
        }
        
        # Send session info
        await websocket.send_json({
            "type": "session_info",
            "info": {
                "profile": session_info.get('profile'),
                "ttl": session_info.get('ttl'),
                "is_interview": session_info.get('is_interview', False)
            }
        })

        # Main loop - bridge WebSocket and SSH
        # Use asyncio.gather for Python 3.8+ compatibility (TaskGroup requires 3.11+)
        ws_to_ssh_task = asyncio.create_task(ws_to_ssh(websocket, ssh_session))
        ssh_to_ws_task = asyncio.create_task(ssh_to_ws(websocket, ssh_session))
        
        try:
            await asyncio.gather(ws_to_ssh_task, ssh_to_ws_task)
        except Exception:
            # Cancel remaining tasks if one fails
            ws_to_ssh_task.cancel()
            ssh_to_ws_task.cancel()
            try:
                await asyncio.gather(ws_to_ssh_task, ssh_to_ws_task, return_exceptions=True)
            except Exception:
                pass

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {session_id}")
    except Exception as e:
        logger.error(f"Error in WebSocket session: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(e)
            })
        except:
            pass
    finally:
        # Cleanup
        if ssh_session:
            ssh_session.stop()
        
        if recorder:
            try:
                recorder.stop_recording(session_id)
            except Exception as e:
                logger.warning(f"Failed to stop recording: {e}")
        
        # Remove key file
        if 'key_path' in locals():
            try:
                os.unlink(key_path)
            except:
                pass
        
        # Remove from active sessions
        active_sessions.pop(session_id, None)


async def ws_to_ssh(websocket: WebSocket, ssh_session: SSHSession):
    """Forward WebSocket input to SSH"""
    try:
        while ssh_session.running:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message.get('type') == 'terminal_input':
                ssh_session.write(message['data'].encode())
            
            elif message.get('type') == 'resize':
                ssh_session.resize(
                    message.get('cols', 80),
                    message.get('rows', 24)
                )
            
            elif message.get('type') == 'chat_message':
                # Broadcast chat to observers
                await broadcast_chat(
                    ssh_session.session_id,
                    "user",
                    message['text']
                )
                
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"ws_to_ssh error: {e}")


async def ssh_to_ws(websocket: WebSocket, ssh_session: SSHSession):
    """Forward SSH output to WebSocket"""
    try:
        while ssh_session.running:
            data = ssh_session.read()
            if data:
                await websocket.send_json({
                    "type": "terminal_output",
                    "data": data.decode('utf-8', errors='replace')
                })
            else:
                await asyncio.sleep(0.01)
    except Exception as e:
        logger.error(f"ssh_to_ws error: {e}")


async def get_session_info(session_id: str) -> Optional[Dict[str, Any]]:
    """Get session info from gateway"""
    import aiohttp
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{GATEWAY_URL}/sessions") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    sessions = data.get('sessions', [])
                    for s in sessions:
                        if s.get('session_id') == session_id:
                            return s
    except Exception as e:
        logger.error(f"Failed to get session info: {e}")
    
    return None


async def broadcast_chat(session_id: str, from_user: str, text: str):
    """Broadcast chat message to all observers"""
    message = json.dumps({
        "type": "chat_message",
        "from": from_user,
        "text": text
    })
    
    # Send to all connected websockets for this session
    for sid, session_data in active_sessions.items():
        if sid == session_id:
            try:
                await session_data["websocket"].send_text(message)
            except Exception as e:
                logger.warning(f"Failed to send chat: {e}")


# Serve main index.html at root
@app.get("/web/", response_class=HTMLResponse)
async def serve_web_terminal():
    """Serve web terminal"""
    index_path = web_static_path / "index.html"
    if index_path.exists():
        return index_path.read_text()
    raise HTTPException(404, "Web terminal not found")


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get('WEB_PORT', 3000)),
        log_level="info"
    )
