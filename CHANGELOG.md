# Changelog

All notable changes to the sshBox project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

#### Core Infrastructure
- **Centralized Configuration Management** (`api/config.py`)
  - Pydantic-based settings with validation
  - Environment variable loading with `SSHBOX_` prefix
  - Nested configuration sections (database, security, storage, provisioner, redis)
  - Startup configuration validation
  - Auto-generation of secure secrets

- **Enhanced Security Module** (`api/security.py`)
  - Constant-time string comparison to prevent timing attacks
  - HMAC-based token creation and validation
  - Token payload object with expiration checking
  - SSH key validator with type and format checking
  - Input validator for session IDs, container names, and paths
  - Path traversal prevention utilities

- **Enhanced Gateway** (`api/gateway_enhanced.py`)
  - Request ID tracking for distributed tracing
  - Structured logging with context
  - Automatic metrics integration
  - Pydantic request/response models with validation
  - Comprehensive error handling
  - Configurable rate limiting per endpoint
  - CORS middleware support
  - Health check with version info
  - Prometheus metrics endpoint

- **Enhanced Metrics** (`api/metrics.py`)
  - Counter metrics (requests, sessions, errors)
  - Gauge metrics (active sessions, DB connections)
  - Histogram metrics (response times, provision times)
  - Prometheus exposition format export
  - JSON file persistence
  - Percentile calculations (p50, p95, p99)
  - Thread-safe operations

- **SSH Session Recorder** (`api/ssh_session_recorder.py`)
  - Asciinema cast format recording
  - Command auditing and tracking
  - SQLite-based metadata storage
  - Retention policy enforcement
  - REST API for recording management
  - Path traversal protection
  - Automatic cleanup of old recordings

- **Enhanced Provisioner** (`api/provisioner_enhanced.py`)
  - Dual runtime support (Docker/Firecracker)
  - Comprehensive input validation
  - Automatic cleanup on failure
  - Detailed error messages
  - Metrics integration
  - Health checks for provisioner availability
  - Timeout handling

#### Testing
- **Enhanced Test Suite** (`tests/test_enhanced.py`)
  - Configuration tests
  - Token validation tests
  - Constant-time operation tests
  - SSH key validation tests
  - Input validation tests
  - Metrics collection tests
  - Integration tests

#### Documentation
- **Technical Review Document** (`docs/TECHNICAL_REVIEW_AND_IMPROVEMENT_PLAN.md`)
  - Comprehensive codebase review findings
  - Security vulnerability assessment
  - Architecture recommendations
  - Priority improvement plan
  - File-by-file action items

- **Enhanced Implementation Guide** (`docs/ENHANCED_IMPLEMENTATION.md`)
  - New features documentation
  - Security improvements
  - Configuration management guide
  - Metrics and observability setup
  - Session recording usage
  - API reference
  - Deployment guide
  - Migration guide

#### Configuration
- **Environment Example File** (`.env.example`)
  - Complete configuration reference
  - Documented environment variables
  - Default values
  - Security recommendations

### Changed

#### Dependencies
- Updated `requirements.txt` with:
  - `pydantic-settings==2.1.0` for configuration management
  - `uvicorn[standard]==0.24.0` for enhanced ASGI features
  - `psycopg2-binary==2.9.9` for PostgreSQL support
  - `python-jose[cryptography]==3.3.0` for JWT support (future use)
  - `passlib[bcrypt]==1.7.4` for password hashing (future use)
  - `cryptography==41.0.7` for cryptographic operations
  - `python-json-logger==2.0.7` for structured logging
  - `pytest==7.4.3`, `pytest-asyncio==0.21.1`, `pytest-cov==4.1.0` for testing
  - `python-dotenv==1.0.0` for environment loading

#### Security
- **Token Validation**
  - Changed from simple string comparison to constant-time comparison
  - Added comprehensive token expiration checking
  - Added profile validation with constant-time comparison
  - Added token replay attack prevention

- **Input Validation**
  - Added strict session ID validation (alphanumeric only, 10-64 chars)
  - Added container name validation (Docker rules)
  - Added path sanitization for all file operations
  - Added SSH key type restrictions

- **Error Handling**
  - Changed from generic error messages to specific error codes
  - Added structured error responses
  - Added request ID to error responses for tracing

#### Configuration
- Changed from hardcoded paths to configurable paths
- Changed from environment variable scattering to centralized configuration
- Changed from silent failures to configuration validation at startup

### Deprecated

- `api/gateway.py` - Original Flask gateway (use `api/gateway_enhanced.py`)
- `api/gateway_fastapi.py` - Original FastAPI gateway (use `api/gateway_enhanced.py`)
- `api/session_recorder.py` - Basic session recorder (use `api/ssh_session_recorder.py`)
- `api/provisioner.py` - Basic provisioner (use `api/provisioner_enhanced.py`)

### Removed

None in this release.

### Fixed

#### Security Fixes
- **SQL Injection Prevention**: Changed f-string SQL queries to parameterized queries
- **Timing Attack Prevention**: Changed all security comparisons to constant-time
- **Path Traversal Prevention**: Added path sanitization for all file operations
- **Command Injection Prevention**: Added strict input validation for shell commands
- **Secret Strength Enforcement**: Added minimum secret length validation

#### Bug Fixes
- **Connection Pool**: Added WAL mode for SQLite for better concurrency
- **Metrics Thread Safety**: Added proper locking for all metric operations
- **Session Cleanup**: Added proper error handling for session destruction
- **Token Validation**: Fixed token age calculation edge cases

### Infrastructure

#### Docker
- Updated Dockerfiles to use new enhanced modules
- Added health check endpoints to all services
- Added resource limits to all containers
- Added logging drivers for better observability

#### Monitoring
- Added Prometheus configuration example
- Added Grafana dashboard JSON template (future)
- Added alerting rules example (future)

---

## [0.2.0] - 2024-01-01

### Added
- FastAPI gateway implementation
- SQLite session recorder
- Connection pooling for database
- Basic metrics collection
- Firecracker microVM support (scripts)
- Session recording metadata
- Rate limiting on API endpoints

### Changed
- Improved token format with HMAC signature
- Enhanced SSH key validation
- Better error messages in provisioner

### Fixed
- Container cleanup on provisioning failure
- Token timestamp validation
- SSH port mapping in provisioner

---

## [0.1.0] - 2023-12-01

### Added
- Initial sshBox implementation
- Basic Flask gateway
- Container provisioner (Docker)
- Invite token CLI
- Session metadata storage
- Basic documentation

---

## Migration Notes

### Migrating from 0.2.0 to Unreleased

1. **Update Environment Variables**

Old:
```bash
GATEWAY_SECRET=mysecret
DB_TYPE=sqlite
```

New:
```bash
SSHBOX_SECURITY_GATEWAY_SECRET=mysecret
SSHBOX_DB_DB_TYPE=sqlite
```

2. **Update Gateway Import**

Old:
```python
from api.gateway_fastapi import app
```

New:
```python
from api.gateway_enhanced import app
```

3. **Update Docker Image**

Old:
```dockerfile
COPY api/gateway_fastapi.py /app/main.py
```

New:
```dockerfile
COPY api/gateway_enhanced.py /app/main.py
```

4. **Create Required Directories**

```bash
sudo mkdir -p /var/lib/sshbox/{recordings,logs,metrics}
sudo chown -R $USER:$USER /var/lib/sshbox
```

5. **Generate Secure Secret**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# Use this value for SSHBOX_SECURITY_GATEWAY_SECRET
```

---

## Security Advisories

### SA-2024-001: Timing Attack Vulnerability

**Severity:** Medium  
**Fixed in:** Unreleased  
**CVE:** Pending

**Description:** Token validation used non-constant-time string comparison, potentially allowing timing attacks to determine valid profiles.

**Mitigation:** Update to latest version with constant-time comparisons.

### SA-2024-002: Path Traversal in Session Recorder

**Severity:** High  
**Fixed in:** Unreleased  
**CVE:** Pending

**Description:** Session recorder did not properly validate file paths, allowing potential path traversal attacks.

**Mitigation:** Update to latest version with path sanitization.

---

## Contributors

This release includes contributions from:
- Core development team
- Security review team
- Community contributors

For a complete list of contributors, see the GitHub contributors page.

---

## License

MIT License - see the LICENSE file for details.
