# sshBox Enhanced Implementation Guide

This document describes the enhanced implementations added to the sshBox project.

## Table of Contents

1. [New Features](#new-features)
2. [Security Improvements](#security-improvements)
3. [Configuration Management](#configuration-management)
4. [Metrics and Observability](#metrics-and-observability)
5. [Session Recording](#session-recording)
6. [Enhanced Provisioning](#enhanced-provisioning)
7. [API Reference](#api-reference)
8. [Deployment](#deployment)

---

## New Features

### 1. Centralized Configuration (`api/config.py`)

A Pydantic-based configuration system with:
- Environment variable loading
- Type validation
- Default values
- Nested configuration sections
- Configuration validation at startup

**Usage:**
```python
from api.config import get_settings

settings = get_settings()
print(settings.gateway_port)
print(settings.security.gateway_secret)
print(settings.database.db_type)
```

**Environment Variables:**
All settings can be configured via environment variables with the `SSHBOX_` prefix:
```bash
export SSHBOX_GATEWAY_PORT=8080
export SSHBOX_SECURITY_GATEWAY_SECRET=your-secret-key
export SSHBOX_DB_DB_TYPE=sqlite
```

### 2. Enhanced Security Module (`api/security.py`)

Comprehensive security utilities including:

#### Token Management
```python
from api.security import create_token, validate_token, TokenValidationError

# Create token
token = create_token(
    secret="your-secret",
    profile="dev",
    ttl=600,
    recipient="user@example.com",
    notes="For testing"
)

# Validate token
try:
    payload = validate_token(token, secret)
    print(f"Profile: {payload.profile}")
    print(f"Expires: {payload.expires_at}")
except TokenValidationError as e:
    print(f"Validation failed: {e.error_code}")
```

#### SSH Key Validation
```python
from api.security import SSHKeyValidator

validator = SSHKeyValidator()
pubkey = "ssh-ed25519 AAAA..."

is_valid, error = validator.validate(pubkey)
if is_valid:
    fingerprint = validator.get_key_fingerprint(pubkey)
    print(f"Valid key with fingerprint: {fingerprint}")
else:
    print(f"Invalid key: {error}")
```

#### Input Validation
```python
from api.security import InputValidator

# Session ID validation
is_valid, error = InputValidator.validate_session_id("abc123def456")

# Container name validation
is_valid, error = InputValidator.validate_container_name("sshbox_test_123")

# Path sanitization (prevent path traversal)
safe_path = InputValidator.sanitize_path("../etc/passwd", "/var/lib/sshbox")
if safe_path is None:
    print("Path traversal attempt detected!")
```

### 3. Enhanced Gateway (`api/gateway_enhanced.py`)

A production-ready FastAPI gateway with:

- **Request ID tracking** - Every request gets a unique ID for tracing
- **Structured logging** - JSON-formatted logs with context
- **Metrics integration** - Automatic request/response metrics
- **Rate limiting** - Configurable per-endpoint rate limits
- **Input validation** - Pydantic models with custom validators
- **Error handling** - Comprehensive error responses
- **Health checks** - Detailed health endpoint
- **Prometheus metrics** - `/metrics` endpoint for monitoring

**Key Features:**

| Feature | Description |
|---------|-------------|
| Request Tracing | X-Request-ID header for distributed tracing |
| Rate Limiting | Per-IP rate limits with configurable thresholds |
| CORS Support | Configurable allowed origins |
| Token Validation | Constant-time comparison to prevent timing attacks |
| Session Management | Automatic cleanup and TTL enforcement |

### 4. Enhanced Metrics (`api/metrics.py`)

Comprehensive metrics collection with:

- **Counter metrics** - Requests, sessions, errors
- **Gauge metrics** - Active sessions, DB connections
- **Histogram metrics** - Response times, provision times
- **Prometheus export** - Standard exposition format
- **JSON persistence** - Metrics saved to file

**Metrics Available:**

```
# Request metrics
sshbox_requests_total
sshbox_requests_successful
sshbox_requests_failed
sshbox_requests_by_endpoint
sshbox_requests_by_status

# Session metrics
sshbox_sessions_created
sshbox_sessions_destroyed
sshbox_active_sessions
sshbox_sessions_by_profile

# Error metrics
sshbox_errors_total
sshbox_errors_by_type

# Timing metrics
sshbox_response_time_avg
sshbox_provision_time_avg

# Database metrics
sshbox_db_connections_active
sshbox_db_connections_idle
```

**Prometheus Integration:**

Add to your `prometheus.yml`:
```yaml
scrape_configs:
  - job_name: 'sshbox'
    static_configs:
      - targets: ['gateway:8080']
    metrics_path: '/metrics'
```

### 5. SSH Session Recorder (`api/ssh_session_recorder.py`)

Full-featured session recording with:

- **Asciinema format** - Standard casting format for playback
- **Command auditing** - Track commands executed
- **SQLite storage** - Persistent metadata storage
- **Retention policies** - Automatic cleanup of old recordings
- **API endpoints** - REST API for managing recordings
- **Path traversal protection** - Secure file access

**Recording Format:**

Sessions are recorded in asciinema cast format (JSON lines):
```json
{"version": 2, "width": 80, "height": 24}
[0.123, "o", "Welcome to sshBox\n"]
[1.456, "i", "ls -la\n"]
[1.789, "o", "total 48\n"]
```

**API Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/recordings/start` | POST | Start a new recording |
| `/recordings/stop/{session_id}` | POST | Stop a recording |
| `/recordings/{session_id}` | GET | Get recording metadata |
| `/recordings/{session_id}/content` | GET | Get recording content |
| `/recordings/{session_id}/commands` | GET | Get executed commands |
| `/recordings` | GET | List all recordings |
| `/cleanup` | POST | Clean up old recordings |

### 6. Enhanced Provisioner (`api/provisioner_enhanced.py`)

Robust container/VM provisioning with:

- **Dual runtime support** - Docker containers or Firecracker VMs
- **Comprehensive validation** - Input validation before provisioning
- **Error handling** - Detailed error messages and cleanup
- **Metrics integration** - Track provision times and success rates
- **Resource cleanup** - Automatic cleanup on failure
- **Health checks** - Verify provisioner availability

**Provisioning Flow:**

```
1. Validate session_id format
2. Validate SSH public key
3. Check resource availability
4. Create container/VM
5. Configure networking
6. Inject SSH key
7. Return connection info
```

**Error Handling:**

- Timeout handling with configurable limits
- Automatic cleanup on failure
- Detailed error messages for debugging
- Metrics tracking for error rates

---

## Security Improvements

### Constant-Time Comparisons

All security-sensitive comparisons use constant-time functions to prevent timing attacks:

```python
# Before (vulnerable)
if profile in allowed_profiles:
    ...

# After (secure)
if constant_time_in(profile, allowed_profiles):
    ...
```

### Input Validation

All user inputs are validated:

```python
# Session ID validation
is_valid, error = InputValidator.validate_session_id(session_id)
if not is_valid:
    raise HTTPException(400, detail=error)

# SSH key validation
is_valid, error = ssh_validator.validate(pubkey)
if not is_valid:
    raise HTTPException(400, detail=error)
```

### Path Traversal Prevention

All file operations validate paths:

```python
safe_path = InputValidator.sanitize_path(user_path, base_dir)
if safe_path is None:
    raise HTTPException(400, detail="Invalid path")
```

### Secret Management

- Auto-generation of secure secrets if not provided
- Minimum length enforcement (32 characters)
- Integration with Vault/AWS Secrets Manager (planned)

---

## Configuration Management

### Environment Variables

All configuration via environment variables:

```bash
# Application
SSHBOX_DEBUG=false
SSHBOX_ENVIRONMENT=production

# Security
SSHBOX_SECURITY_GATEWAY_SECRET=your-secret-key
SSHBOX_SECURITY_TOKEN_MAX_AGE=300

# Database
SSHBOX_DB_DB_TYPE=sqlite
SSHBOX_DB_SQLITE_PATH=/var/lib/sshbox/sessions.db

# Storage
SSHBOX_STORAGE_RECORDINGS_DIR=/var/lib/sshbox/recordings
SSHBOX_STORAGE_LOGS_DIR=/var/log/sshbox

# Provisioner
SSHBOX_PROVISIONER_PROVISIONER_TYPE=container
SSHBOX_PROVISIONER_DEFAULT_TTL=1800
```

### Configuration File

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
# Edit .env with your settings
```

---

## Deployment

### Docker Deployment

Updated `docker-compose.yml` supports the enhanced services:

```yaml
services:
  gateway:
    build:
      context: .
      dockerfile: images/Dockerfile.gateway
    environment:
      - SSHBOX_SECURITY_GATEWAY_SECRET=${GATEWAY_SECRET}
      - SSHBOX_DB_DB_TYPE=sqlite
      - SSHBOX_STORAGE_LOGS_DIR=/app/logs
    volumes:
      - /var/lib/sshbox:/var/lib/sshbox
    ports:
      - "8080:8080"
```

### Kubernetes Deployment

Example deployment manifest:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sshbox-gateway
spec:
  replicas: 3
  selector:
    matchLabels:
      app: sshbox-gateway
  template:
    metadata:
      labels:
        app: sshbox-gateway
    spec:
      containers:
      - name: gateway
        image: sshbox/gateway:latest
        env:
        - name: SSHBOX_SECURITY_GATEWAY_SECRET
          valueFrom:
            secretKeyRef:
              name: sshbox-secrets
              key: gateway-secret
        ports:
        - containerPort: 8080
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 10
```

---

## Testing

### Run Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test class
pytest tests/test_enhanced.py::TestTokenValidation -v

# Run with coverage
pytest tests/ --cov=api --cov-report=html
```

### Test Coverage

Current test coverage:
- Configuration: ✅
- Token validation: ✅
- SSH key validation: ✅
- Input validation: ✅
- Metrics collection: ✅
- Integration tests: ✅

---

## Migration Guide

### From Old Gateway to Enhanced Gateway

1. Update imports:
```python
# Old
from api.gateway_fastapi import app

# New
from api.gateway_enhanced import app
```

2. Update configuration:
```bash
# Old environment variables
GATEWAY_SECRET=xxx
DB_TYPE=sqlite

# New environment variables
SSHBOX_SECURITY_GATEWAY_SECRET=xxx
SSHBOX_DB_DB_TYPE=sqlite
```

3. Update Docker image:
```dockerfile
# Old
COPY api/gateway_fastapi.py /app/main.py

# New
COPY api/gateway_enhanced.py /app/main.py
```

---

## Troubleshooting

### Common Issues

**Configuration Validation Failed:**
```
Configuration validation failed:
- Directory not writable: /var/lib/sshbox/recordings
```
**Solution:** Ensure directories exist and are writable:
```bash
sudo mkdir -p /var/lib/sshbox/recordings
sudo chown -R $USER:$USER /var/lib/sshbox
```

**Secret Too Short:**
```
ValueError: gateway_secret must be at least 32 characters
```
**Solution:** Generate a secure secret:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Metrics File Not Writable:**
```
Error saving metrics to /var/lib/sshbox/metrics.json
```
**Solution:** Check directory permissions:
```bash
ls -la /var/lib/sshbox/
chmod 755 /var/lib/sshbox/
```

---

## API Reference

### Gateway Endpoints

#### POST /request
Request a new SSH box.

**Request:**
```json
{
  "token": "dev:600:1234567890:abc123:none:signature",
  "pubkey": "ssh-ed25519 AAAA...",
  "profile": "dev",
  "ttl": 600
}
```

**Response:**
```json
{
  "host": "192.168.1.100",
  "port": 2222,
  "user": "boxuser",
  "session_id": "box_1234567890_abc",
  "profile": "dev",
  "expires_at": "2024-01-01T12:00:00Z"
}
```

#### GET /sessions
List sessions.

**Query Parameters:**
- `status` (optional): Filter by status

**Response:**
```json
[
  {
    "session_id": "box_1234567890_abc",
    "container_name": "sshbox_box_1234567890_abc",
    "profile": "dev",
    "status": "active",
    "time_left": 300
  }
]
```

#### POST /destroy
Destroy a session.

**Request:**
```json
{
  "session_id": "box_1234567890_abc"
}
```

#### GET /health
Health check.

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2024-01-01T12:00:00Z",
  "version": "1.0.0",
  "environment": "production"
}
```

#### GET /metrics
Prometheus metrics.

**Response:** Plain text in Prometheus exposition format.

---

## Contributing

### Code Style

- Use type hints for all function signatures
- Follow PEP 8 style guidelines
- Add docstrings to all public functions
- Write tests for new features

### Pull Request Process

1. Create feature branch
2. Make changes
3. Add tests
4. Run tests: `pytest tests/ -v`
5. Run linter: `flake8 api/`
6. Submit PR

---

## License

MIT License - see the LICENSE file for details.
