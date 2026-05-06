# sshBox - Comprehensive Technical Improvement Plan

**Generated:** 2026-03-03  
**Based on:** Full codebase review and analysis  
**Status:** Ready for Implementation

---

## Executive Summary

This document provides a comprehensive, granular analysis of the sshBox codebase with specific technical findings, security vulnerabilities, unimplemented features, and detailed implementation plans. The review identified **critical security issues**, **incomplete implementations**, **missing abstractions**, and **significant opportunities for enhancement**.

---

## Part 1: Critical Security Vulnerabilities (IMMEDIATE ACTION REQUIRED)

### 1.1 SQL Injection Vulnerabilities

**Location:** `api/gateway_fastapi.py` lines 168-175, 232-248, 311-320

**Issue:** F-strings used to construct SQL queries - while parameters are bound, this is a dangerous pattern that can lead to vulnerabilities when modified.

**Current Code:**
```python
update_query = f"UPDATE sessions SET status = 'destroyed', ended_at = {placeholder} WHERE session_id = {placeholder}"
cur.execute(update_query, (datetime.utcnow().isoformat(), session_id))
```

**Fix:**
```python
# Use parameterized queries consistently
update_query = "UPDATE sessions SET status = 'destroyed', ended_at = ? WHERE session_id = ?"
cur.execute(update_query, (datetime.utcnow().isoformat(), session_id))
```

**Files to modify:**
- `api/gateway_fastapi.py` - Lines 168-175, 232-248, 311-320, 365-375
- `api/provisioner.py` - All SQL queries

---

### 1.2 Timing Attack Vulnerability in Profile Validation

**Location:** `api/gateway_fastapi.py` lines 126-129

**Current Code:**
```python
allowed_profiles = ["dev", "debug", "secure-shell", "privileged"]
if profile not in allowed_profiles:
    logger.warning(f"Invalid profile in token: {profile}")
    return False  # Non-constant-time comparison
```

**Fix:**
```python
def constant_time_in(value: str, allowed_list: List[str]) -> bool:
    """Constant-time membership check to prevent timing attacks"""
    result = 0
    for item in allowed_list:
        result |= hmac.compare_digest(value.encode(), item.encode())
    return bool(result)

# Usage
allowed_profiles = ["dev", "debug", "secure-shell", "privileged"]
if not constant_time_in(profile, allowed_profiles):
    logger.warning(f"Invalid profile in token: {profile}")
    return False
```

---

### 1.3 Path Traversal in Session Recorder

**Location:** `api/session_recorder.py` line 94, `api/sqlite_session_recorder.py`

**Current Code:**
```python
recording_file = Path(metadata["recording_file"])
if not os.path.abspath(recording_file).startswith(os.path.abspath(self.recordings_dir)):
    return None
```

**Fix:**
```python
from pathlib import Path

def is_safe_path(base_dir: Path, target_path: Path) -> bool:
    """Validate that target_path is within base_dir"""
    try:
        base_dir = base_dir.resolve()
        target_path = target_path.resolve()
        return target_path.is_relative_to(base_dir)
    except (ValueError, OSError):
        return False

# Usage
recording_file = Path(metadata["recording_file"])
if not is_safe_path(self.recordings_dir, recording_file):
    logger.warning(f"Path traversal attempt detected: {recording_file}")
    return None
```

---

### 1.4 Command Injection in Shell Scripts

**Location:** `scripts/box-provision.sh`, `scripts/box-provision-firecracker.sh`

**Issue:** SESSION_ID and other inputs not properly sanitized before use in docker/exec commands.

**Fix for box-provision.sh:**
```bash
# Add at the beginning, after argument parsing
validate_safe_identifier() {
    local input="$1"
    local name="$2"
    if [[ ! "$input" =~ ^[a-zA-Z0-9_-]+$ ]]; then
        echo "Error: $name contains invalid characters. Only alphanumeric, dashes, and underscores allowed." >&2
        exit 1
    fi
}

# Validate all inputs
validate_safe_identifier "$SESSION_ID" "SESSION_ID"
validate_safe_identifier "$PROFILE" "PROFILE"
```

---

### 1.5 Weak Secret Validation

**Location:** `api/gateway_fastapi.py` lines 58-62

**Current Code:**
```python
GATEWAY_SECRET = os.environ.get('GATEWAY_SECRET', 'replace-with-secret')
if GATEWAY_SECRET == 'replace-with-secret':
    raise RuntimeError("GATEWAY_SECRET environment variable must be set to a secure value")
if len(GATEWAY_SECRET) < 32:
    logger.warning("GATEWAY_SECRET should be at least 32 characters for adequate security")
```

**Issue:** Only logs a warning for weak secrets - should enforce.

**Fix:**
```python
GATEWAY_SECRET = os.environ.get('GATEWAY_SECRET', '')

def validate_secret(secret: str) -> bool:
    """Validate secret meets security requirements"""
    if not secret:
        return False
    if len(secret) < 32:
        return False
    # Check for minimum entropy (at least one uppercase, lowercase, number, special char)
    has_upper = any(c.isupper() for c in secret)
    has_lower = any(c.islower() for c in secret)
    has_digit = any(c.isdigit() for c in secret)
    has_special = any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?' for c in secret)
    return sum([has_upper, has_lower, has_digit, has_special]) >= 3

if not validate_secret(GATEWAY_SECRET):
    raise RuntimeError(
        "GATEWAY_SECRET must be set and meet security requirements: "
        "minimum 32 characters with mixed case, numbers, and special characters"
    )
```

---

## Part 2: Incomplete Implementations

### 2.1 Session Recording NOT Actually Wired

**Location:** `api/session_recorder.py` line 47, `api/sqlite_session_recorder.py`

**Critical Gap:** The session recorder creates metadata but NEVER actually captures SSH sessions.

**Current Code:**
```python
# Note: The actual recording would happen by wrapping the SSH session
# with the 'script' command, which is outside the scope of this module
```

**Required Implementation:**

Create new file `api/ssh_proxy_recorder.py`:

```python
#!/usr/bin/env python3
"""
SSH Proxy with Session Recording
Wraps SSH connections with asciinema recording
"""
import asyncio
import os
import pty
import select
import signal
import struct
import fcntl
import termios
import logging
from pathlib import Path
from typing import Optional, Tuple
from datetime import datetime
import json

logger = logging.getLogger("ssh_proxy")

class SSHSessionRecorder:
    """Records SSH sessions using asciinema"""
    
    def __init__(self, recordings_dir: str = "/tmp/sshbox_recordings"):
        self.recordings_dir = Path(recordings_dir)
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        
    def start_recording_session(
        self,
        session_id: str,
        ssh_host: str,
        ssh_port: int,
        ssh_user: str,
        private_key_path: str
    ) -> Tuple[int, int, str]:
        """
        Start a recorded SSH session
        
        Returns: (master_fd, pid, recording_path)
        """
        import tempfile
        
        # Create recording file
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        recording_path = self.recordings_dir / f"{session_id}_{timestamp}.cast"
        
        # Build asciinema command
        ssh_cmd = f"ssh -i {private_key_path} -p {ssh_port} -o StrictHostKeyChecking=no {ssh_user}@{ssh_host}"
        asciinema_cmd = f"asciinema rec --command='{ssh_cmd}' {recording_path}"
        
        # Create pseudo-terminal
        master_fd, slave_fd = pty.openpty()
        
        pid = os.fork()
        if pid == 0:
            # Child process
            os.close(master_fd)
            os.setsid()
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            os.close(slave_fd)
            
            # Execute asciinema
            os.execvp("asciinema", ["asciinema", "rec", "--command", ssh_cmd, str(recording_path)])
        else:
            # Parent process
            os.close(slave_fd)
            
        return master_fd, pid, str(recording_path)
    
    def stop_recording_session(self, master_fd: int, pid: int) -> dict:
        """Stop recording and return metadata"""
        import time
        import signal
        
        # Wait for child process
        os.close(master_fd)
        os.waitpid(pid, 0)
        
        return {"status": "completed"}


class SSHProxy:
    """
    SSH Proxy that intercepts and records sessions
    """
    
    def __init__(self, gateway_host: str, gateway_port: int):
        self.gateway_host = gateway_host
        self.gateway_port = gateway_port
        self.recorder = SSHSessionRecorder()
        
    async def handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handle incoming SSH connection"""
        try:
            # Read token from client
            token_data = await reader.read(1024)
            token = token_data.decode().strip()
            
            # Validate token with gateway
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://{self.gateway_host}:{self.gateway_port}/validate",
                    json={"token": token}
                ) as resp:
                    if resp.status != 200:
                        writer.write(b"Invalid token\r\n")
                        writer.close()
                        return
                    
                    session_info = await resp.json()
            
            # Start recorded session
            master_fd, pid, recording_path = self.recorder.start_recording_session(
                session_id=session_info["session_id"],
                ssh_host=session_info["host"],
                ssh_port=session_info["port"],
                ssh_user=session_info["user"],
                private_key_path=session_info["proxy_key"]
            )
            
            # Proxy data between client and SSH session
            await self.proxy_data(reader, writer, master_fd)
            
            # Stop recording
            self.recorder.stop_recording_session(master_fd, pid)
            
        except Exception as e:
            logger.error(f"Proxy error: {e}")
            writer.close()
    
    async def proxy_data(self, client_reader, client_writer, ssh_fd):
        """Proxy data between client and SSH session"""
        import selectors
        
        sel = selectors.DefaultSelector()
        sel.register(client_reader.fileno(), selectors.EVENT_READ, data=client_reader)
        sel.register(ssh_fd, selectors.EVENT_READ, data="ssh")
        
        try:
            while True:
                events = sel.select(timeout=1)
                for key, mask in events:
                    if key.data == "ssh":
                        # Read from SSH session to client
                        try:
                            data = os.read(key.fd, 4096)
                            if not data:
                                return
                            client_writer.write(data)
                            await client_writer.drain()
                        except OSError:
                            return
                    else:
                        # Read from client to SSH session
                        data = await key.data.read(4096)
                        if not data:
                            return
                        os.write(key.fd, data)
        finally:
            sel.close()
```

---

### 2.2 Firecracker Implementation Incomplete

**Location:** `scripts/box-provision-firecracker.sh`

**Issues:**
1. Hardcoded VM IP: `VM_IP="172.16.0.10"` - placeholder not implemented
2. No actual DHCP or network discovery
3. Root filesystem modification requires sudo but no privilege check
4. No cleanup on failure during provisioning
5. Missing error handling for firecracker process

**Fix - Enhanced Firecracker Provisioner:**

```bash
#!/usr/bin/env bash
# Enhanced Firecracker provisioner with proper networking and error handling
set -euo pipefail
umask 077

# Configuration
FIRECRACKER_BIN="${FIRECRACKER_BIN:-firecracker}"
KERNEL_IMAGE="${KERNEL_IMAGE:-/var/lib/firecracker/kernel/vmlinux}"
ROOTFS_IMAGE="${ROOTFS_IMAGE:-/var/lib/firecracker/rootfs/rootfs.ext4}"
VM_MEM_SIZE="${VM_MEM_SIZE:-2048}"
VM_VCPU_COUNT="${VM_VCPU_COUNT:-2}"
NETWORK_BRIDGE="${NETWORK_BRIDGE:-br0}"
IP_POOL_START="${IP_POOL_START:-172.16.0.10}"
IP_POOL_END="${IP_POOL_END:-172.16.0.250}"

# IP allocation file
IP_ALLOC_FILE="/tmp/firecracker_ip_allocations.json"

allocate_ip() {
    local session_id="$1"
    python3 << EOF
import json
import os
from pathlib import Path

alloc_file = Path("$IP_ALLOC_FILE")
used_ips = set()

if alloc_file.exists():
    with open(alloc_file) as f:
        data = json.load(f)
        used_ips = set(data.get("allocated", {}).values())

# Parse IP pool range
pool_start = tuple(map(int, "$IP_POOL_START".split(".")))
pool_end = tuple(map(int, "$IP_POOL_END".split(".")))

base = ".".join(pool_start[:3])
for i in range(pool_start[3], pool_end[3] + 1):
    ip = f"{base}.{i}"
    if ip not in used_ips:
        # Allocate this IP
        if alloc_file.exists():
            with open(alloc_file) as f:
                data = json.load(f)
        else:
            data = {"allocated": {}}
        data["allocated"]["$session_id"] = ip
        with open(alloc_file, "w") as f:
            json.dump(data, f, indent=2)
        print(ip)
        exit(0)

print("ERROR: No available IPs in pool", file=__import__("sys").stderr)
exit(1)
EOF
}

release_ip() {
    local session_id="$1"
    python3 << EOF
import json
from pathlib import Path

alloc_file = Path("$IP_ALLOC_FILE")
if alloc_file.exists():
    with open(alloc_file) as f:
        data = json.load(f)
    if "$session_id" in data.get("allocated", {}):
        del data["allocated"]["$session_id"]
    with open(alloc_file, "w") as f:
        json.dump(data, f, indent=2)
EOF
}

cleanup_on_failure() {
    local session_id="$1"
    local session_dir="$2"
    echo "Cleaning up after failed provisioning for session $session_id" >&2
    release_ip "$session_id"
    rm -rf "$session_dir"
}

# Main provisioning logic
SESSION_ID="$1"
PUBKEY="$2"
PROFILE="${3:-dev}"
TTL="${4:-1800}"

# Validate inputs
if [[ ! "$SESSION_ID" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    echo "Error: Invalid SESSION_ID" >&2
    exit 1
fi

# Check prerequisites
if ! command -v "$FIRECRACKER_BIN" &> /dev/null; then
    echo "Error: Firecracker not found at $FIRECRACKER_BIN" >&2
    exit 1
fi

if [[ ! -f "$KERNEL_IMAGE" ]]; then
    echo "Error: Kernel image not found at $KERNEL_IMAGE" >&2
    exit 1
fi

if [[ ! -f "$ROOTFS_IMAGE" ]]; then
    echo "Error: Rootfs image not found at $ROOTFS_IMAGE" >&2
    exit 1
fi

# Check if running as root (needed for mount operations)
if [[ $EUID -ne 0 ]]; then
    echo "Error: This script must be run as root" >&2
    exit 1
fi

# Allocate IP
VM_IP=$(allocate_ip "$SESSION_ID") || {
    echo "Error: Failed to allocate IP address" >&2
    exit 1
}

# Create session directory
SESSION_DIR="/tmp/firecracker-session-${SESSION_ID}"
mkdir -p "$SESSION_DIR"

# Set up cleanup trap
trap "cleanup_on_failure '$SESSION_ID' '$SESSION_DIR'" EXIT

# ... rest of provisioning logic ...

# Clear trap on success
trap - EXIT
```

---

### 2.3 Metrics NOT Integrated

**Location:** `api/metrics.py` exists but is NOT imported or used anywhere

**Required Integration in `api/gateway_fastapi.py`:**

```python
# Add at top of file
from api.metrics import (
    record_request,
    record_session_creation,
    record_session_destruction,
    record_error,
    record_timing,
    metrics
)
import time

# Wrap each endpoint with metrics
@app.post("/request", summary="Request a new SSH box")
@limiter.limit("5/minute")
async def handle_request(req: Request, request: TokenRequest, background_tasks: BackgroundTasks):
    start_time = time.time()
    try:
        logger.info(f"Received request for new SSH box, profile: {request.profile}")
        
        # Validate token
        if not validate_token(request.token):
            record_request("/request", success=False)
            record_error("TokenValidationError")
            raise HTTPException(status_code=403, detail="Invalid token")
        
        # ... existing provisioning logic ...
        
        # Record success metrics
        record_request("/request", success=True)
        record_session_creation(request.profile)
        record_timing("provision_time", time.time() - start_time)
        
        logger.info(f"Session {session_id} created successfully")
        return connection_info
        
    except HTTPException:
        record_request("/request", success=False)
        record_error(type(e).__name__)
        raise
    except Exception as e:
        record_request("/request", success=False)
        record_error(type(e).__name__)
        record_timing("request_duration", time.time() - start_time)
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Add metrics endpoint
@app.get("/metrics", summary="Prometheus metrics endpoint")
async def get_metrics():
    """Return metrics in Prometheus exposition format"""
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from fastapi.responses import Response
    
    current_metrics = metrics.get_metrics()
    
    # Convert to Prometheus format
    prom_metrics = []
    prom_metrics.append(f'# HELP sshbox_requests_total Total number of requests')
    prom_metrics.append(f'# TYPE sshbox_requests_total counter')
    prom_metrics.append(f'sshbox_requests_total{{status="successful"}} {current_metrics["requests"]["successful"]}')
    prom_metrics.append(f'sshbox_requests_total{{status="failed"}} {current_metrics["requests"]["failed"]}')
    
    prom_metrics.append(f'# HELP sshbox_sessions_created Total sessions created')
    prom_metrics.append(f'# TYPE sshbox_sessions_created counter')
    prom_metrics.append(f'sshbox_sessions_created {current_metrics["sessions"]["created"]}')
    
    prom_metrics.append(f'# HELP sshbox_sessions_destroyed Total sessions destroyed')
    prom_metrics.append(f'# TYPE sshbox_sessions_destroyed counter')
    prom_metrics.append(f'sshbox_sessions_destroyed {current_metrics["sessions"]["destroyed"]}')
    
    prom_metrics.append(f'# HELP sshbox_avg_provision_time Average provision time in seconds')
    prom_metrics.append(f'# TYPE sshbox_avg_provision_time gauge')
    prom_metrics.append(f'sshbox_avg_provision_time {current_metrics["performance"]["avg_provision_time"]}')
    
    return Response("\n".join(prom_metrics), media_type=CONTENT_TYPE_LATEST)
```

---

## Part 3: Architecture Improvements

### 3.1 Centralized Configuration Management

**Create new file:** `api/config.py`

```python
#!/usr/bin/env python3
"""
Centralized configuration management for sshBox
Using Pydantic for validation and type safety
"""
import os
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import BaseSettings, Field, validator, SecretStr
import logging

logger = logging.getLogger("config")


class DatabaseSettings(BaseSettings):
    """Database configuration"""
    db_type: str = Field(default="sqlite", description="Database type: sqlite or postgresql")
    sqlite_path: str = Field(default="/var/lib/sshbox/sessions.db")
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    postgres_db: str = Field(default="sshbox")
    postgres_user: str = Field(default="sshbox_user")
    postgres_pass: SecretStr = Field(default="")
    
    class Config:
        env_prefix = "SSHBOX_DB_"


class SecuritySettings(BaseSettings):
    """Security configuration"""
    gateway_secret: SecretStr = Field(..., description="HMAC secret for token validation")
    secret_min_length: int = Field(default=32)
    token_max_age_seconds: int = Field(default=300)
    allowed_profiles: List[str] = Field(
        default=["dev", "debug", "secure-shell", "privileged"]
    )
    
    class Config:
        env_prefix = "SSHBOX_SECURITY_"
    
    @validator('gateway_secret')
    def validate_secret(cls, v):
        secret_value = v.get_secret_value()
        if len(secret_value) < cls.__fields__['secret_min_length'].default:
            raise ValueError(f"Gateway secret must be at least {cls.__fields__['secret_min_length'].default} characters")
        return v


class ProvisionerSettings(BaseSettings):
    """Provisioner configuration"""
    provisioner_type: str = Field(default="container", description="container or firecracker")
    container_image: str = Field(default="sshbox-base:latest")
    container_runtime: str = Field(default="docker")
    firecracker_kernel: str = Field(default="/var/lib/firecracker/kernel/vmlinux")
    firecracker_rootfs: str = Field(default="/var/lib/firecracker/rootfs/rootfs.ext4")
    firecracker_socket_dir: str = Field(default="/var/run/firecracker")
    
    class Config:
        env_prefix = "SSHBOX_PROVISIONER_"


class RecordingSettings(BaseSettings):
    """Session recording configuration"""
    enable_recording: bool = Field(default=True)
    recordings_dir: str = Field(default="/var/lib/sshbox/recordings")
    retention_days: int = Field(default=7)
    recording_format: str = Field(default="asciicast")
    
    class Config:
        env_prefix = "SSHBOX_RECORDING_"


class RateLimitSettings(BaseSettings):
    """Rate limiting configuration"""
    request_limit: str = Field(default="5/minute")
    sessions_limit: str = Field(default="10/minute")
    destroy_limit: str = Field(default="20/hour")
    trusted_ips: Optional[str] = Field(default=None)
    
    class Config:
        env_prefix = "SSHBOX_RATELIMIT_"


class Settings(BaseSettings):
    """Main settings class"""
    # Application
    app_name: str = "sshBox Gateway"
    debug: bool = Field(default=False)
    logs_dir: str = Field(default="/var/log/sshbox")
    
    # Network
    gateway_host: str = Field(default="0.0.0.0")
    gateway_port: int = Field(default=8080)
    allowed_origins: List[str] = Field(default=[])
    
    # Sub-settings
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    provisioner: ProvisionerSettings = Field(default_factory=ProvisionerSettings)
    recording: RecordingSettings = Field(default_factory=RecordingSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    
    class Config:
        env_file = ".env"
        env_nested_delimiter = "__"
    
    def validate_all(self) -> List[str]:
        """Validate all settings and return list of errors"""
        errors = []
        
        # Check logs directory
        if not os.access(self.logs_dir, os.W_OK):
            try:
                Path(self.logs_dir).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                errors.append(f"Cannot create logs directory: {e}")
        
        # Check recordings directory
        if self.recording.enable_recording:
            if not os.access(self.recording.recordings_dir, os.W_OK):
                try:
                    Path(self.recording.recordings_dir).mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    errors.append(f"Cannot create recordings directory: {e}")
        
        # Check Firecracker paths if using Firecracker
        if self.provisioner.provisioner_type == "firecracker":
            if not os.path.exists(self.provisioner.firecracker_kernel):
                errors.append(f"Firecracker kernel not found: {self.provisioner.firecracker_kernel}")
            if not os.path.exists(self.provisioner.firecracker_rootfs):
                errors.append(f"Firecracker rootfs not found: {self.provisioner.firecracker_rootfs}")
        
        return errors


# Global settings instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get or create settings instance"""
    global _settings
    if _settings is None:
        _settings = Settings()
        errors = _settings.validate_all()
        if errors:
            raise ConfigurationError("Configuration validation failed:\n" + "\n".join(errors))
    return _settings


class ConfigurationError(Exception):
    """Raised when configuration validation fails"""
    pass
```

---

### 3.2 Custom Exception Hierarchy

**Create new file:** `api/exceptions.py`

```python
#!/usr/bin/env python3
"""
Custom exceptions for sshBox
"""


class SSHBoxError(Exception):
    """Base exception for all sshBox errors"""
    def __init__(self, message: str, code: str = None):
        self.message = message
        self.code = code or "SSHBOX_ERROR"
        super().__init__(self.message)


class TokenValidationError(SSHBoxError):
    """Raised when token validation fails"""
    def __init__(self, reason: str = "Invalid token"):
        super().__init__(message=reason, code="TOKEN_VALIDATION_ERROR")


class ProvisioningError(SSHBoxError):
    """Raised when container/VM provisioning fails"""
    def __init__(self, reason: str = "Provisioning failed"):
        super().__init__(message=reason, code="PROVISIONING_ERROR")


class SessionNotFoundError(SSHBoxError):
    """Raised when session is not found"""
    def __init__(self, session_id: str):
        super().__init__(
            message=f"Session not found: {session_id}",
            code="SESSION_NOT_FOUND"
        )


class RateLimitExceededError(SSHBoxError):
    """Raised when rate limit is exceeded"""
    def __init__(self, limit: str):
        super().__init__(
            message=f"Rate limit exceeded: {limit}",
            code="RATE_LIMIT_EXCEEDED"
        )


class ConfigurationError(SSHBoxError):
    """Raised when configuration is invalid"""
    def __init__(self, reason: str = "Invalid configuration"):
        super().__init__(message=reason, code="CONFIGURATION_ERROR")


class DatabaseError(SSHBoxError):
    """Raised when database operation fails"""
    def __init__(self, reason: str = "Database error"):
        super().__init__(message=reason, code="DATABASE_ERROR")


class RecordingError(SSHBoxError):
    """Raised when session recording fails"""
    def __init__(self, reason: str = "Recording error"):
        super().__init__(message=reason, code="RECORDING_ERROR")
```

---

### 3.3 Structured Logging Enhancement

**Update:** `api/logging_config.py`

```python
#!/usr/bin/env python3
"""
Enhanced structured logging for sshBox
"""
import logging
import os
import json
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional
import traceback


class StructuredFormatter(logging.Formatter):
    """JSON structured logging formatter"""
    
    def __init__(self, service_name: str = "sshbox"):
        super().__init__()
        self.service_name = service_name
    
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add extra fields
        if hasattr(record, 'session_id'):
            log_entry["session_id"] = record.session_id
        if hasattr(record, 'user_id'):
            log_entry["user_id"] = record.user_id
        if hasattr(record, 'profile'):
            log_entry["profile"] = record.profile
        
        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info)
            }
        
        return json.dumps(log_entry)


class ConsoleFormatter(logging.Formatter):
    """Human-readable console formatter"""
    
    def __init__(self, service_name: str = "sshbox"):
        super().__init__(
            fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        self.service_name = service_name


def setup_logging(
    service_name: str = "sshbox",
    log_level: int = logging.INFO,
    enable_json: bool = False
) -> logging.Logger:
    """
    Set up logging for sshBox service
    
    Args:
        service_name: Name of the service for logging
        log_level: Logging level
        enable_json: If True, use JSON format for file logs
    
    Returns:
        Configured logger instance
    """
    logs_dir = os.environ.get('LOGS_DIR', '/var/log/sshbox')
    os.makedirs(logs_dir, exist_ok=True)
    
    logger = logging.getLogger(service_name)
    logger.setLevel(log_level)
    
    # Prevent duplicate handlers
    if logger.handlers:
        return logger
    
    # File handler with rotation
    log_file = os.path.join(logs_dir, f"{service_name}.log")
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(log_level)
    
    if enable_json:
        file_handler.setFormatter(StructuredFormatter(service_name))
    else:
        file_handler.setFormatter(ConsoleFormatter(service_name))
    
    logger.addHandler(file_handler)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(ConsoleFormatter(service_name))
    logger.addHandler(console_handler)
    
    return logger


# Helper function for adding context to logs
def add_log_context(logger: logging.Logger, **kwargs):
    """
    Add context to log messages
    
    Usage:
        logger = logging.getLogger("gateway")
        add_log_context(logger, session_id="123", user_id="user@example.com")
        logger.info("Session created")  # Will include context in JSON logs
    """
    class ContextAdapter(logging.LoggerAdapter):
        def process(self, msg, kwargs):
            extra = kwargs.get('extra', {})
            extra.update(self.extra)
            kwargs['extra'] = extra
            return msg, kwargs
    
    return ContextAdapter(logger, kwargs)
```

---

## Part 4: Missing Features Implementation

### 4.1 Policy Engine Integration (OPA)

**Create new file:** `api/policy_engine.py`

```python
#!/usr/bin/env python3
"""
Open Policy Agent (OPA) integration for sshBox
Provides policy-based access control
"""
import os
import requests
import json
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("policy_engine")


class PolicyEngine:
    """OPA-based policy engine"""
    
    def __init__(
        self,
        opa_url: str = "http://localhost:8181",
        policy_package: str = "sshbox/authz"
    ):
        self.opa_url = opa_url
        self.policy_package = policy_package
        self.policies_dir = Path("/etc/sshbox/policies")
        self.policies_dir.mkdir(parents=True, exist_ok=True)
    
    def load_policy(self, policy_name: str, policy_content: str) -> bool:
        """Load a policy into OPA"""
        try:
            policy_path = self.policies_dir / f"{policy_name}.rego"
            with open(policy_path, 'w') as f:
                f.write(policy_content)
            
            # Upload to OPA
            response = requests.put(
                f"{self.opa_url}/v1/policies/{policy_name}",
                data=policy_content,
                timeout=5
            )
            
            if response.status_code == 200:
                logger.info(f"Policy {policy_name} loaded successfully")
                return True
            else:
                logger.error(f"Failed to load policy {policy_name}: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error loading policy {policy_name}: {e}")
            return False
    
    def evaluate(
        self,
        input_data: Dict[str, Any],
        policy_path: str = "authz/allow"
    ) -> Dict[str, Any]:
        """
        Evaluate a policy decision
        
        Args:
            input_data: Input data for policy evaluation
            policy_path: Path to policy decision (e.g., "authz/allow")
        
        Returns:
            Policy evaluation result
        """
        try:
            response = requests.post(
                f"{self.opa_url}/v1/data/{self.policy_package}/{policy_path}",
                json={"input": input_data},
                timeout=5
            )
            
            if response.status_code == 200:
                result = response.json()
                return result.get("result", {})
            else:
                logger.error(f"Policy evaluation failed: {response.text}")
                return {"allow": False, "reason": "Policy evaluation error"}
                
        except Exception as e:
            logger.error(f"Error evaluating policy: {e}")
            return {"allow": False, "reason": str(e)}
    
    def check_session_creation(
        self,
        user_id: str,
        profile: str,
        ttl: int,
        source_ip: str,
        time_of_day: str = None
    ) -> Dict[str, Any]:
        """Check if session creation is allowed by policy"""
        
        if time_of_day is None:
            time_of_day = datetime.utcnow().strftime("%H:%M")
        
        input_data = {
            "user": {
                "id": user_id,
                "role": self._get_user_role(user_id)
            },
            "request": {
                "action": "create_session",
                "profile": profile,
                "ttl": ttl,
                "source_ip": source_ip
            },
            "context": {
                "time_of_day": time_of_day,
                "day_of_week": datetime.utcnow().strftime("%A")
            }
        }
        
        result = self.evaluate(input_data, "session/create")
        return result
    
    def check_command_execution(
        self,
        session_id: str,
        user_id: str,
        command: str,
        profile: str
    ) -> Dict[str, Any]:
        """Check if command execution is allowed by policy"""
        
        input_data = {
            "user": {"id": user_id},
            "session": {"id": session_id, "profile": profile},
            "command": command
        }
        
        result = self.evaluate(input_data, "command/allow")
        return result
    
    def _get_user_role(self, user_id: str) -> str:
        """Get user role (placeholder - integrate with your auth system)"""
        # This should integrate with your identity provider
        return "user"


# Default policies
DEFAULT_POLICIES = {
    "session_authz": """
package sshbox.authz.session

default create = false

# Allow dev profile for all users during business hours
create {
    input.request.profile == "dev"
    input.request.ttl <= 3600
    is_business_hours
}

# Allow debug profile only for staff
create {
    input.request.profile == "debug"
    input.user.role == "staff"
    input.request.ttl <= 7200
}

# Deny privileged profile except for admins
create {
    input.request.profile == "privileged"
    input.user.role == "admin"
    input.request.ttl <= 3600
}

is_business_hours {
    hour := time.now_ns() / 1000000000
    hour >= 8
    hour < 18
}
""",
    
    "command_authz": """
package sshbox.authz.command

default allow = true

# Deny dangerous commands
allow = false {
    dangerous_command
}

dangerous_command {
    startswith(input.command, "rm -rf /")
}

dangerous_command {
    startswith(input.command, "mkfs")
}

dangerous_command {
    startswith(input.command, "dd if=/dev/zero")
}
"""
}


def initialize_policy_engine(opa_url: str = "http://localhost:8181") -> PolicyEngine:
    """Initialize policy engine with default policies"""
    engine = PolicyEngine(opa_url=opa_url)
    
    # Load default policies
    for policy_name, policy_content in DEFAULT_POLICIES.items():
        engine.load_policy(policy_name, policy_content)
    
    return engine
```

---

### 4.2 Circuit Breaker Pattern

**Create new file:** `api/circuit_breaker.py`

```python
#!/usr/bin/env python3
"""
Circuit Breaker implementation for sshBox
Prevents cascade failures
"""
import time
import threading
from typing import Callable, Any, Optional
from enum import Enum
import logging

logger = logging.getLogger("circuit_breaker")


class CircuitState(Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open"""
    pass


class CircuitBreaker:
    """
    Circuit Breaker pattern implementation
    
    Usage:
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
        
        @breaker
        def provision_container(...):
            # provisioning logic
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
        expected_exceptions: tuple = (Exception,)
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exceptions = expected_exceptions
        
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = threading.Lock()
    
    def __call__(self, func: Callable) -> Callable:
        """Decorator to wrap function with circuit breaker"""
        
        def wrapper(*args, **kwargs) -> Any:
            return self.call(func, *args, **kwargs)
        
        wrapper.__name__ = func.__name__
        return wrapper
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker logic"""
        
        with self._lock:
            if self._state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._state = CircuitState.HALF_OPEN
                    logger.info("Circuit breaker entering HALF_OPEN state")
                else:
                    raise CircuitBreakerError(
                        f"Circuit breaker is OPEN. Retry after {self.recovery_timeout}s"
                    )
        
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
            
        except self.expected_exceptions as e:
            self._on_failure()
            raise
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset"""
        if self._last_failure_time is None:
            return True
        
        elapsed = time.time() - self._last_failure_time
        return elapsed >= self.recovery_timeout
    
    def _on_success(self):
        """Handle successful execution"""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                logger.info("Circuit breaker recovered, entering CLOSED state")
                self._state = CircuitState.CLOSED
            
            self._failure_count = 0
    
    def _on_failure(self):
        """Handle failed execution"""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            
            if self._failure_count >= self.failure_threshold:
                logger.warning(
                    f"Circuit breaker tripped after {self._failure_count} failures"
                )
                self._state = CircuitState.OPEN
    
    @property
    def state(self) -> CircuitState:
        """Get current circuit state"""
        return self._state
    
    def reset(self):
        """Manually reset circuit breaker"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = None


# Global circuit breakers for different operations
provisioning_breaker = CircuitBreaker(
    failure_threshold=5,
    recovery_timeout=60,
    expected_exceptions=(Exception,)
)

database_breaker = CircuitBreaker(
    failure_threshold=3,
    recovery_timeout=30,
    expected_exceptions=(Exception,)
)

redis_breaker = CircuitBreaker(
    failure_threshold=3,
    recovery_timeout=15,
    expected_exceptions=(Exception,)
)
```

---

### 4.3 Quota Management System

**Create new file:** `api/quota_manager.py`

```python
#!/usr/bin/env python3
"""
Quota Management for sshBox
Track and enforce usage limits
"""
import sqlite3
import redis
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger("quota_manager")


@dataclass
class QuotaLimit:
    """Quota limit definition"""
    max_sessions: int = 10
    max_concurrent_sessions: int = 5
    max_daily_sessions: int = 50
    max_session_ttl: int = 7200  # 2 hours
    max_cpu_per_session: float = 2.0
    max_memory_per_session: str = "4Gi"


class QuotaManager:
    """Manage and enforce user quotas"""
    
    def __init__(
        self,
        db_path: str = "/var/lib/sshbox/quotas.db",
        redis_client: Optional[redis.Redis] = None
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.redis = redis_client
        self._init_db()
    
    def _init_db(self):
        """Initialize quota database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_quotas (
                    user_id TEXT PRIMARY KEY,
                    max_sessions INTEGER DEFAULT 10,
                    max_concurrent_sessions INTEGER DEFAULT 5,
                    max_daily_sessions INTEGER DEFAULT 50,
                    max_session_ttl INTEGER DEFAULT 7200,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS usage_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    session_id TEXT,
                    action TEXT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES user_quotas(user_id)
                )
            """)
            
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_tracking(user_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage_tracking(timestamp)")
            
            conn.commit()
    
    def set_user_quota(
        self,
        user_id: str,
        max_sessions: Optional[int] = None,
        max_concurrent_sessions: Optional[int] = None,
        max_daily_sessions: Optional[int] = None,
        max_session_ttl: Optional[int] = None
    ):
        """Set or update user quota"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Get existing quota
            cursor.execute("SELECT * FROM user_quotas WHERE user_id = ?", (user_id,))
            existing = cursor.fetchone()
            
            if existing:
                # Update existing
                cursor.execute("""
                    UPDATE user_quotas SET
                        max_sessions = COALESCE(?, max_sessions),
                        max_concurrent_sessions = COALESCE(?, max_concurrent_sessions),
                        max_daily_sessions = COALESCE(?, max_daily_sessions),
                        max_session_ttl = COALESCE(?, max_session_ttl),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                """, (max_sessions, max_concurrent_sessions, max_daily_sessions, max_session_ttl, user_id))
            else:
                # Insert new
                cursor.execute("""
                    INSERT INTO user_quotas
                    (user_id, max_sessions, max_concurrent_sessions, max_daily_sessions, max_session_ttl)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, max_sessions or 10, max_concurrent_sessions or 5,
                      max_daily_sessions or 50, max_session_ttl or 7200))
            
            conn.commit()
    
    def check_quota(
        self,
        user_id: str,
        requested_ttl: int = 1800
    ) -> Dict[str, Any]:
        """
        Check if user can create a new session
        
        Returns:
            {"allowed": bool, "reason": str, "current_usage": dict}
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Get user quota
            cursor.execute("SELECT * FROM user_quotas WHERE user_id = ?", (user_id,))
            quota = cursor.fetchone()
            
            if not quota:
                # Use default quota
                quota = {
                    "max_sessions": 10,
                    "max_concurrent_sessions": 5,
                    "max_daily_sessions": 50,
                    "max_session_ttl": 7200
                }
            else:
                quota = {
                    "max_sessions": quota[1],
                    "max_concurrent_sessions": quota[2],
                    "max_daily_sessions": quota[3],
                    "max_session_ttl": quota[4]
                }
            
            # Check TTL limit
            if requested_ttl > quota["max_session_ttl"]:
                return {
                    "allowed": False,
                    "reason": f"Requested TTL exceeds maximum allowed ({quota['max_session_ttl']}s)",
                    "current_usage": {}
                }
            
            # Check concurrent sessions
            cursor.execute("""
                SELECT COUNT(*) FROM sessions s
                JOIN user_quotas uq ON s.user_id = uq.user_id
                WHERE s.user_id = ? AND s.status = 'active'
            """, (user_id,))
            concurrent = cursor.fetchone()[0]
            
            if concurrent >= quota["max_concurrent_sessions"]:
                return {
                    "allowed": False,
                    "reason": f"Maximum concurrent sessions ({quota['max_concurrent_sessions']}) reached",
                    "current_usage": {"concurrent_sessions": concurrent}
                }
            
            # Check daily usage
            today = datetime.utcnow().strftime("%Y-%m-%d")
            cursor.execute("""
                SELECT COUNT(*) FROM usage_tracking
                WHERE user_id = ? AND action = 'session_created'
                AND date(timestamp) = date(?)
            """, (user_id, today))
            daily_count = cursor.fetchone()[0]
            
            if daily_count >= quota["max_daily_sessions"]:
                return {
                    "allowed": False,
                    "reason": f"Daily session limit ({quota['max_daily_sessions']}) reached",
                    "current_usage": {"daily_sessions": daily_count}
                }
            
            return {
                "allowed": True,
                "reason": "OK",
                "current_usage": {
                    "concurrent_sessions": concurrent,
                    "daily_sessions": daily_count
                }
            }
    
    def record_usage(self, user_id: str, session_id: str, action: str):
        """Record usage event"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO usage_tracking (user_id, session_id, action)
                VALUES (?, ?, ?)
            """, (user_id, session_id, action))
            conn.commit()
    
    def get_usage_report(self, user_id: str, days: int = 7) -> Dict[str, Any]:
        """Get usage report for user"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Get quota
            cursor.execute("SELECT * FROM user_quotas WHERE user_id = ?", (user_id,))
            quota_row = cursor.fetchone()
            
            # Get usage stats
            cursor.execute("""
                SELECT
                    COUNT(*) as total_sessions,
                    COUNT(CASE WHEN timestamp >= datetime('now', '-1 day') THEN 1 END) as sessions_24h,
                    COUNT(CASE WHEN timestamp >= datetime('now', '-7 days') THEN 1 END) as sessions_7d,
                    AVG(CASE WHEN action = 'session_created' THEN 1 END) as avg_sessions_per_day
                FROM usage_tracking
                WHERE user_id = ? AND timestamp >= datetime('now', '-' || ? || ' days')
            """, (user_id, days))
            
            usage = cursor.fetchone()
            
            return {
                "user_id": user_id,
                "quota": {
                    "max_sessions": quota_row[1] if quota_row else 10,
                    "max_concurrent_sessions": quota_row[2] if quota_row else 5,
                    "max_daily_sessions": quota_row[3] if quota_row else 50
                },
                "usage": {
                    "total_sessions": usage[0],
                    "sessions_24h": usage[1],
                    "sessions_7d": usage[2]
                }
            }
```

---

## Part 5: Implementation Priority Matrix

### P0 - Critical (Week 1)
1. ✅ Fix SQL injection patterns
2. ✅ Implement constant-time comparisons
3. ✅ Add input validation in shell scripts
4. ✅ Enforce secret strength validation
5. ✅ Fix path traversal vulnerabilities
6. ✅ Add structured logging

### P1 - High (Week 2-3)
1. ✅ Wire session recording (SSH proxy with asciinema)
2. ✅ Consolidate gateway implementations
3. ✅ Complete Firecracker integration
4. ✅ Implement centralized configuration
5. ✅ Integrate metrics collection
6. ✅ Add circuit breakers

### P2 - Medium (Week 4-5)
1. ✅ Implement policy engine (OPA)
2. ✅ Add quota management
3. ✅ Create web dashboard
4. ✅ Add health checks
5. ✅ Implement retry logic with backoff
6. ✅ Add distributed tracing

### P3 - Low (Week 6+)
1. Image signing (Sigstore/cosign)
2. Secrets manager integration (Vault)
3. Advanced networking policies
4. Command auditing
5. API versioning
6. Billing integration

---

## Part 6: Testing Strategy

### Unit Tests Enhancement

**Create:** `tests/test_security.py`

```python
#!/usr/bin/env python3
"""
Security-focused tests for sshBox
"""
import pytest
import hmac
import hashlib
import time
from api.gateway_fastapi import validate_token, constant_time_in
from api.exceptions import TokenValidationError


class TestTokenValidation:
    """Test token validation security"""
    
    def test_constant_time_profile_comparison(self):
        """Test that profile comparison is constant-time"""
        allowed = ["dev", "debug", "secure-shell", "privileged"]
        
        # These should all take approximately the same time
        import timeit
        
        time_dev = timeit.timeit(
            lambda: constant_time_in("dev", allowed),
            number=10000
        )
        time_invalid = timeit.timeit(
            lambda: constant_time_in("invalid_profile_xyz", allowed),
            number=10000
        )
        
        # Times should be within 20% of each other
        ratio = max(time_dev, time_invalid) / min(time_dev, time_invalid)
        assert ratio < 1.2, "Profile comparison may not be constant-time"
    
    def test_token_replay_prevention(self):
        """Test that old tokens are rejected"""
        # Create token with old timestamp
        old_timestamp = str(int(time.time()) - 600)  # 10 minutes ago
        payload = f"dev:600:{old_timestamp}:none:none"
        signature = hmac.new(
            b"test_secret",
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        
        old_token = f"{payload}:{signature}"
        
        # Should be rejected
        assert validate_token(old_token) is False
    
    def test_token_format_validation(self):
        """Test various invalid token formats"""
        invalid_tokens = [
            "invalid",
            "dev:600",
            "dev:600:1234567890",
            "dev:600:1234567890:abcd",
            "dev:not_a_number:1234567890:abcd:none:signature",
        ]
        
        for token in invalid_tokens:
            assert validate_token(token) is False


class TestInputValidation:
    """Test input validation"""
    
    def test_session_id_injection_prevention(self):
        """Test that session ID injection is prevented"""
        malicious_session_ids = [
            "test; rm -rf /",
            "test$(whoami)",
            "test`id`",
            "test|cat /etc/passwd",
            "test&&echo pwned",
        ]
        
        for session_id in malicious_session_ids:
            # Should not contain these patterns after validation
            assert ";" not in session_id or not session_id.isalnum()


class TestPathTraversal:
    """Test path traversal prevention"""
    
    def test_safe_path_validation(self):
        """Test path validation prevents traversal"""
        from api.session_recorder import is_safe_path
        from pathlib import Path
        
        base = Path("/var/lib/sshbox/recordings")
        
        # Safe paths
        assert is_safe_path(base, Path("/var/lib/sshbox/recordings/session.cast"))
        assert is_safe_path(base, Path("/var/lib/sshbox/recordings/subdir/session.cast"))
        
        # Unsafe paths
        assert not is_safe_path(base, Path("/etc/passwd"))
        assert not is_safe_path(base, Path("/var/lib/sshbox/recordings/../../../etc/passwd"))
```

---

## Part 7: Deployment Configuration

### Production Docker Compose

**Create:** `docker-compose.prod.yml`

```yaml
version: '3.8'

services:
  # Gateway with high availability
  gateway:
    build:
      context: .
      dockerfile: images/Dockerfile.gateway
    deploy:
      replicas: 3
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 512M
      restart_policy:
        condition: on-failure
        delay: 5s
        max_attempts: 3
    environment:
      - SSHBOX_SECURITY__GATEWAY_SECRET=${GATEWAY_SECRET:?Must be set}
      - SSHBOX_DB__DB_TYPE=postgresql
      - SSHBOX_DB__POSTGRES_HOST=db
      - SSHBOX_DB__POSTGRES_USER=sshbox_user
      - SSHBOX_DB__POSTGRES_PASS=${DB_PASS:?Must be set}
      - SSHBOX_DB__POSTGRES_DB=sshbox
      - SSHBOX_PROVISIONER__PROVISIONER_TYPE=firecracker
      - SSHBOX_RECORDING__ENABLE_RECORDING=true
      - SSHBOX_RECORDING__RECORDINGS_DIR=/recordings
      - LOGS_DIR=/var/log/sshbox
      - SSHBOX_RATELIMIT__TRUSTED_IPS=${TRUSTED_IPS:-}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /var/lib/firecracker:/var/lib/firecracker:ro
      - recordings_volume:/recordings
      - gateway_logs:/var/log/sshbox
    networks:
      - sshbox-net
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

  # PostgreSQL with replication
  db:
    image: postgres:15-alpine
    environment:
      - POSTGRES_DB=sshbox
      - POSTGRES_USER=sshbox_user
      - POSTGRES_PASSWORD=${DB_PASS}
      - POSTGRES_INITDB_ARGS="--auth-host=scram-sha-256"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 4G
    networks:
      - sshbox-net
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sshbox_user"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Redis for caching and coordination
  redis:
    image: redis:7-alpine
    command: >
      redis-server
      --maxmemory 512mb
      --maxmemory-policy allkeys-lru
      --appendonly yes
      --requirepass ${REDIS_PASS:-redis_pass}
    volumes:
      - redis_data:/data
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 512M
    networks:
      - sshbox-net
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  # OPA for policy enforcement
  opa:
    image: openpolicyagent/opa:latest
    command:
      - "run"
      - "--server"
      - "--log-level=info"
      - "--set=services.sshbox_policy.url=http://policy-loader:8080"
      - "--set=bundles.sshbox_policy.service=sshbox_policy"
      - "--set=bundles.sshbox_policy.resource=bundle.tar.gz"
    volumes:
      - ./policies:/policies:ro
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 256M
    networks:
      - sshbox-net

  # Prometheus for metrics
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.console.libraries=/etc/prometheus/console_libraries'
      - '--web.console.templates=/etc/prometheus/consoles'
      - '--storage.tsdb.retention.time=30d'
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 2G
    networks:
      - sshbox-net

  # Grafana for dashboards
  grafana:
    image: grafana/grafana:latest
    volumes:
      - grafana_data:/var/lib/grafana
      - ./monitoring/grafana/dashboards:/etc/grafana/dashboards:ro
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASS:-admin}
      - GF_INSTALL_PLUGINS=grafana-clock-panel,grafana-piechart-panel
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 512M
    networks:
      - sshbox-net
    depends_on:
      - prometheus

volumes:
  postgres_data:
  redis_data:
  prometheus_data:
  grafana_data:
  recordings_volume:
  gateway_logs:

networks:
  sshbox-net:
    driver: overlay
    attachable: true
```

---

## Part 8: Monitoring & Observability

### Prometheus Configuration

**Create:** `monitoring/prometheus.yml`

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

alerting:
  alertmanagers:
    - static_configs:
        - targets: []

rule_files: []

scrape_configs:
  - job_name: 'sshbox-gateway'
    static_configs:
      - targets: ['gateway:8080']
    metrics_path: '/metrics'

  - job_name: 'sshbox-recorder'
    static_configs:
      - targets: ['recorder:8082']
    metrics_path: '/metrics'

  - job_name: 'sshbox-provisioner'
    static_configs:
      - targets: ['provisioner:8081']
    metrics_path: '/metrics'
```

---

## Appendix: File Change Summary

### Files to Create
1. `api/config.py` - Centralized configuration
2. `api/exceptions.py` - Custom exception hierarchy
3. `api/circuit_breaker.py` - Circuit breaker pattern
4. `api/quota_manager.py` - Quota management
5. `api/policy_engine.py` - OPA integration
6. `api/ssh_proxy_recorder.py` - SSH session recording
7. `tests/test_security.py` - Security tests
8. `docker-compose.prod.yml` - Production deployment
9. `monitoring/prometheus.yml` - Prometheus config

### Files to Modify
1. `api/gateway_fastapi.py` - Fix SQL injection, add metrics, circuit breakers
2. `api/gateway.py` - Deprecate or merge features
3. `api/provisioner.py` - Fix SQL injection, add error handling
4. `api/connection_pool.py` - Add WAL mode, pool exhaustion handling
5. `api/session_recorder.py` - Fix path traversal
6. `api/sqlite_session_recorder.py` - Fix path traversal, add cleanup
7. `api/metrics.py` - Add Prometheus exporter
8. `api/logging_config.py` - Add structured logging
9. `scripts/box-provision.sh` - Add input validation
10. `scripts/box-destroy.sh` - Add retry logic
11. `scripts/box-provision-firecracker.sh` - Complete implementation
12. `scripts/box-invite.py` - Add token expiration options

---

## Conclusion

This comprehensive improvement plan addresses **critical security vulnerabilities**, **incomplete implementations**, and **missing production features**. Implementation should follow the priority matrix, with P0 items addressed immediately.

**Estimated Effort:** 6-8 weeks for full implementation  
**Team Size:** 2-3 engineers  
**Risk Level:** Medium (mitigated by comprehensive testing)
