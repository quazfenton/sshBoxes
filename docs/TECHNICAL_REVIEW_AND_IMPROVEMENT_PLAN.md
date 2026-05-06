# sshBox Technical Review & Improvement Plan

**Date:** 2026-03-03  
**Reviewer:** AI Code Analysis  
**Scope:** Full codebase review for security, completeness, extensibility, and production readiness

---

## Executive Summary

The sshBox project provides ephemeral SSH containers/VMs with a solid foundation but has several areas requiring improvement for production readiness:

### Key Findings:
1. **Multiple gateway implementations** - Both Flask (`gateway.py`) and FastAPI (`gateway_fastapi.py`) exist with inconsistent features
2. **Incomplete session recording** - Recording infrastructure exists but actual SSH session capture is not wired
3. **Security gaps** - Several input validation and injection vulnerabilities
4. **Missing abstractions** - Hard-coded paths, missing configuration management
5. **Unfinished Firecracker integration** - VM provisioning has placeholder code
6. **No policy engine** - Profile enforcement is minimal
7. **Missing observability** - Metrics collection exists but not integrated

---

## Detailed Technical Findings

### 1. Gateway Layer Issues

#### 1.1 Duplicate Gateway Implementations
**Files:** `api/gateway.py`, `api/gateway_fastapi.py`

**Issues:**
- Two separate gateway implementations (Flask and FastAPI) with feature divergence
- `gateway.py` uses simpler 4-part token format vs `gateway_fastapi.py` 6-part format
- No unified interface or abstraction layer
- Docker compose references both patterns inconsistently

**Recommendation:**
- Consolidate to single FastAPI implementation
- Create abstract base class for gateway functionality
- Implement strategy pattern for token validation

#### 1.2 Token Validation Security
**File:** `api/gateway_fastapi.py` lines 92-138

**Issues:**
```python
# Current implementation has timing-based profile validation
allowed_profiles = ["dev", "debug", "secure-shell", "privileged"]
if profile not in allowed_profiles:
    logger.warning(f"Invalid profile in token: {profile}")
    return False  # Non-constant-time comparison
```

**Security Concerns:**
- Profile validation uses non-constant-time comparison (potential timing attack)
- Token timestamp validation window (5 min) may be too restrictive or permissive
- No rate limiting per token (only per IP)
- Token replay protection relies solely on timestamp

**Fix Required:**
```python
# Use constant-time comparison for all validations
def constant_time_in(value, allowed_list):
    """Constant-time membership check"""
    result = 0
    for item in allowed_list:
        result |= hmac.compare_digest(value.encode(), item.encode())
    return bool(result)
```

#### 1.3 SQL Injection Prevention
**File:** `api/gateway_fastapi.py` lines 168-175, 232-248

**Current Code:**
```python
# VULNERABLE: f-string with user input in query
update_query = f"UPDATE sessions SET status = 'destroyed', ended_at = {placeholder} WHERE session_id = {placeholder}"
cur.execute(update_query, (datetime.utcnow().isoformat(), session_id))
```

**Issue:** While parameters are properly bound, the query structure uses f-strings which is a bad pattern that could lead to vulnerabilities if modified.

**Fix:** Use parameterized queries consistently:
```python
query = "UPDATE sessions SET status = 'destroyed', ended_at = ? WHERE session_id = ?"
```

#### 1.4 Rate Limiting Configuration
**File:** `api/gateway_fastapi.py`

**Issues:**
- Rate limits are hard-coded decorators
- No configuration for different environments
- No whitelist for trusted IPs
- Rate limit exceeded handler returns generic error

**Recommendation:**
```python
# Configurable rate limits
RATE_LIMITS = {
    "request": os.environ.get("RATE_LIMIT_REQUEST", "5/minute"),
    "sessions": os.environ.get("RATE_LIMIT_SESSIONS", "10/minute"),
    "destroy": os.environ.get("RATE_LIMIT_DESTROY", "20/hour"),
}

# Add IP whitelist
TRUSTED_IPS = os.environ.get("TRUSTED_IPS", "").split(",")
```

---

### 2. Provisioner Issues

#### 2.1 Command Injection Vulnerability
**File:** `scripts/box-provision.sh`

**Issue:** Session ID and other inputs are not properly sanitized before use in docker commands.

**Current:**
```bash
CONTAINER_NAME="box_${SESSION_ID}"
docker run -d --name "$CONTAINER_NAME" ...
```

**Risk:** If SESSION_ID contains special characters, could lead to command injection.

**Fix:** Add strict validation:
```bash
# Validate SESSION_ID contains only safe characters
if [[ ! "$SESSION_ID" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    echo "Error: Invalid SESSION_ID" >&2
    exit 1
fi
```

#### 2.2 Incomplete Firecracker Implementation
**File:** `scripts/box-provision-firecracker.sh`

**Issues:**
- Hardcoded VM IP: `VM_IP="172.16.0.10"` - placeholder not implemented
- No actual DHCP or network discovery
- Root filesystem modification requires sudo but no privilege check
- No cleanup on failure during provisioning
- Missing error handling for firecracker process

**Required Implementation:**
```bash
# Add network namespace setup
# Implement proper IP allocation from pool
# Add cleanup trap for partial failures
# Implement firecracker process monitoring
```

#### 2.3 Resource Leak in Destroy Script
**File:** `scripts/box-destroy.sh`

**Issue:** Background destroy scheduling has no error handling:
```bash
( sleep "$TTL"; ./box-destroy.sh "$CONTAINER_NAME" "$METADATA_FILE" 2>/dev/null || true ) & disown
```

**Problems:**
- No logging of destruction failures
- No retry mechanism
- No notification if cleanup fails
- Process orphaned with `disown`

---

### 3. Session Recording Gaps

#### 3.1 Recording Not Actually Wired
**File:** `api/session_recorder.py`, `api/sqlite_session_recorder.py`

**Critical Gap:** The session recorder creates metadata but never actually captures SSH sessions.

**Current Code (session_recorder.py line 47):**
```python
# Note: The actual recording would happen by wrapping the SSH session
# with the 'script' command, which is outside the scope of this module
```

**Required Implementation:**
1. SSH proxy that wraps connections with `script` command
2. Asciinema casting integration
3. Real-time streaming to storage
4. Session playback API

#### 3.2 Path Traversal Risk
**File:** `api/session_recorder.py` line 94

**Issue:**
```python
recording_file = Path(metadata["recording_file"])
# Validation exists but is string-based, not pathlib
if not os.path.abspath(recording_file).startswith(os.path.abspath(self.recordings_dir)):
    return None
```

**Fix:** Use `Path.resolve().is_relative_to()` for Python 3.9+

---

### 4. Connection Pool Issues

#### 4.1 SQLite Thread Safety
**File:** `api/connection_pool.py`

**Issue:** SQLite connections created with `check_same_thread=False` but no WAL mode or proper isolation level.

**Missing Configuration:**
```python
conn = sqlite3.connect(
    self.db_path,
    check_same_thread=False,
    isolation_level=None,  # Enable autocommit
    timeout=30.0
)
# Enable WAL mode for better concurrency
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
```

#### 4.2 Pool Exhaustion Handling
**Issue:** When pool is exhausted, new connections are created without limit until `max_connections`.

**Fix:** Implement proper backpressure:
```python
if self.active_connections >= self.max_connections:
    # Wait with timeout for available connection
    # Or raise PoolExhaustedError
```

---

### 5. Metrics & Observability Gaps

#### 5.1 Metrics Not Integrated
**File:** `api/metrics.py`

**Issue:** Metrics collector exists but is not imported or used in gateway/provisioner.

**Required Integration:**
```python
# In gateway_fastapi.py
from api.metrics import record_request, record_session_creation, record_error

@app.post("/request")
async def handle_request(...):
    start_time = time.time()
    try:
        # ... processing
        record_request("/request", success=True)
        record_session_creation(request.profile)
    except Exception as e:
        record_error(type(e).__name__)
        record_request("/request", success=False)
    finally:
        record_timing("request_duration", time.time() - start_time)
```

#### 5.2 No Prometheus/Export Format
**Issue:** Metrics stored as JSON, not exposed in Prometheus format.

**Required:** Add `/metrics` endpoint with Prometheus exposition format.

---

### 6. Security Vulnerabilities

#### 6.1 SSH Key Injection
**File:** `scripts/box-provision.sh` lines 52-63

**Issue:** Public key is passed directly to container without validation beyond format check.

**Missing Validations:**
- Key type restrictions (no DSA, minimum RSA 2048)
- Key option restrictions (no command=, no port forwarding options)
- Rate limiting on key injection

#### 6.2 Container Escape Risk
**File:** `images/Dockerfile`

**Issue:** Container has capabilities that could enable escape:
```dockerfile
capabilities:
  - "NET_ADMIN"
  - "SYS_PTRACE"
```

**Recommendation:**
- Remove capabilities by default
- Use seccomp profiles
- Enable AppArmor/SELinux profiles
- Use user namespaces

#### 6.3 Secrets Management
**File:** Multiple files

**Issues:**
- `GATEWAY_SECRET` defaults to `'replace-with-secret'` or `'mysecret'`
- Database passwords in docker-compose.yml
- No integration with Vault/AWS Secrets Manager

**Required:**
```python
# Enforce secret strength
if len(GATEWAY_SECRET) < 32:
    raise RuntimeError("GATEWAY_SECRET must be at least 32 characters")

# Check for secrets manager
if os.environ.get("VAULT_ADDR"):
    # Fetch from Vault
    GATEWAY_SECRET = fetch_from_vault("sshbox/gateway/secret")
```

---

### 7. Configuration Management

#### 7.1 Hard-coded Paths
**Files:** Multiple

**Examples:**
- `/tmp/sshbox_sessions.db`
- `/tmp/sshbox_recordings`
- `/tmp/sshbox_logs`

**Fix:** Centralized configuration:
```python
# config.py
from pydantic import BaseSettings

class Settings(BaseSettings):
    # Database
    db_type: str = "sqlite"
    sqlite_path: str = "/var/lib/sshbox/sessions.db"
    
    # Recordings
    recordings_dir: str = "/var/lib/sshbox/recordings"
    
    # Logs
    logs_dir: str = "/var/log/sshbox"
    
    # Security
    gateway_secret: str
    secret_min_length: int = 32
    
    class Config:
        env_prefix = "SSHBOX_"
        env_file = ".env"

settings = Settings()
```

#### 7.2 No Configuration Validation
**Issue:** Invalid configurations fail at runtime.

**Required:** Startup validation:
```python
def validate_config():
    errors = []
    if not os.access(settings.recordings_dir, os.W_OK):
        errors.append(f"Recordings directory not writable: {settings.recordings_dir}")
    if len(settings.gateway_secret) < settings.secret_min_length:
        errors.append(f"Gateway secret too short (min {settings.secret_min_length})")
    if errors:
        raise ConfigurationError("\n".join(errors))
```

---

### 8. Missing Features

#### 8.1 No Policy Engine
**Referenced in docs but not implemented:**
- OPA (Open Policy Agent) integration
- Risk-based access control
- Command allowlisting/blocklisting
- Network policy enforcement

#### 8.2 No Web Dashboard
**Referenced in docker-compose.yml but Dockerfile missing:**
- `images/Dockerfile.dashboard` referenced but content minimal
- No frontend code
- No authentication for dashboard

#### 8.3 No Billing/Quota System
**Mentioned in docs but not implemented:**
- Usage tracking
- Quota enforcement
- Cost allocation tags

#### 8.4 No Image Signing
**Mentioned in security docs:**
- No image signature verification
- No supply chain security

---

### 9. Testing Gaps

#### 9.1 Incomplete Test Coverage
**Files:** `tests/test_api.py`, `tests/test_sshbox.py`

**Issues:**
- Tests mock token validation instead of testing real validation
- No integration tests for complete flow
- No security penetration tests
- No load/performance tests

#### 9.2 Missing Test Scenarios
- Concurrent session creation
- Resource exhaustion scenarios
- Network partition handling
- Database corruption recovery

---

### 10. Documentation Gaps

#### 10.1 Missing Documentation
- API reference documentation
- Deployment guide for production
- Disaster recovery procedures
- Security hardening guide
- Troubleshooting guide

---

## Priority Improvement Plan

### Phase 1: Critical Security Fixes (Week 1-2)

1. **Fix SQL injection patterns** - Replace all f-string queries
2. **Implement constant-time comparisons** - For all security-sensitive checks
3. **Add input validation** - Strict validation for all user inputs
4. **Enforce secret strength** - Validate GATEWAY_SECRET at startup
5. **Fix path traversal** - Use proper pathlib for all file operations
6. **Add audit logging** - Log all security-relevant events

### Phase 2: Core Functionality (Week 3-4)

1. **Wire session recording** - Implement SSH proxy with recording
2. **Consolidate gateways** - Single FastAPI implementation
3. **Fix Firecracker integration** - Complete VM provisioning
4. **Add configuration management** - Pydantic settings
5. **Integrate metrics** - Wire metrics throughout codebase
6. **Add health checks** - Comprehensive health endpoint

### Phase 3: Production Readiness (Week 5-6)

1. **Add policy engine** - OPA integration
2. **Implement quotas** - Usage tracking and limits
3. **Add circuit breakers** - Prevent cascade failures
4. **Implement retry logic** - With exponential backoff
5. **Add distributed tracing** - OpenTelemetry integration
6. **Create runbooks** - Operations documentation

### Phase 4: Extended Features (Week 7-8)

1. **Web dashboard** - Complete frontend implementation
2. **Image signing** - Sigstore/cosign integration
3. **Secrets manager** - Vault/AWS integration
4. **Advanced networking** - Network policies
5. **Command auditing** - Per-command logging
6. **API versioning** - Backward compatibility

---

## Code Quality Recommendations

### 1. Add Type Hints
```python
# Before
def validate_token(token):

# After
def validate_token(token: str) -> bool:
```

### 2. Use Context Managers
```python
# Before
conn = get_db_connection()
try:
    # ...
finally:
    conn.close()

# After
with get_db_connection() as conn:
    # ...
```

### 3. Structured Logging
```python
# Before
logger.info(f"Session {session_id} created")

# After
logger.info("session_created", extra={
    "session_id": session_id,
    "profile": profile,
    "ttl": ttl
})
```

### 4. Error Handling
```python
# Add custom exceptions
class SSHBoxError(Exception):
    pass

class ProvisioningError(SSHBoxError):
    pass

class TokenValidationError(SSHBoxError):
    pass
```

---

## Third-Party Integration Opportunities

### 1. Observability Stack
- **Prometheus** - Metrics collection
- **Grafana** - Dashboards
- **Jaeger** - Distributed tracing
- **ELK Stack** - Log aggregation

### 2. Security Tools
- **HashiCorp Vault** - Secrets management
- **Open Policy Agent** - Policy engine
- **Sigstore/Cosign** - Image signing
- **Falco** - Runtime security

### 3. Cloud Integrations
- **AWS Systems Manager** - Session Manager integration
- **Azure Arc** - Hybrid management
- **GCP Cloud Audit Logs** - Audit integration

### 4. Developer Experience
- **Teleport** - SSH certificate authority
- **ngrok** - Tunnel integration for testing
- **GitHub Actions** - CI/CD templates

---

## Architecture Recommendations

### 1. Microservices Separation
```
Current: Monolithic API modules
Recommended:
- sshbox-gateway (HTTP/SSH gateway)
- sshbox-provisioner (Container/VM provisioning)
- sshbox-recorder (Session recording)
- sshbox-auditor (Audit logging)
- sshbox-policy (Policy enforcement)
```

### 2. Message Queue Integration
```
Add Redis Streams or RabbitMQ for:
- Asynchronous provisioning
- Event-driven cleanup
- Audit log buffering
```

### 3. Database Strategy
```
Current: SQLite/PostgreSQL
Recommended:
- PostgreSQL for primary data
- Redis for caching/sessions
- TimescaleDB for metrics
- S3 for recordings
```

---

## Conclusion

The sshBox project has a solid foundation but requires significant work for production readiness. Priority should be given to:

1. **Security fixes** - Address all identified vulnerabilities
2. **Core functionality** - Complete session recording and Firecracker support
3. **Observability** - Integrate metrics, logging, and tracing
4. **Documentation** - Create comprehensive operational guides

Estimated effort: 8 weeks for minimum viable production release.

---

## Appendix: File-by-File Action Items

### api/gateway_fastapi.py
- [ ] Fix SQL injection patterns
- [ ] Add constant-time comparisons
- [ ] Integrate metrics
- [ ] Add structured logging
- [ ] Implement circuit breaker
- [ ] Add request tracing IDs

### api/gateway.py
- [ ] Deprecate in favor of FastAPI version
- [ ] OR merge features and consolidate

### api/provisioner.py
- [ ] Add error handling
- [ ] Implement retry logic
- [ ] Add resource validation
- [ ] Integrate metrics

### api/connection_pool.py
- [ ] Enable WAL mode
- [ ] Add pool exhaustion handling
- [ ] Add connection health checks
- [ ] Add metrics

### api/session_recorder.py
- [ ] Implement actual SSH session capture
- [ ] Add asciinema integration
- [ ] Fix path traversal
- [ ] Add streaming support

### api/sqlite_session_recorder.py
- [ ] Add cleanup job
- [ ] Implement playback API
- [ ] Add compression
- [ ] Add S3 export

### api/metrics.py
- [ ] Add Prometheus exporter
- [ ] Integrate with gateway
- [ ] Add histograms
- [ ] Add custom metrics

### scripts/box-provision.sh
- [ ] Add strict input validation
- [ ] Implement cleanup on failure
- [ ] Add logging
- [ ] Add resource checks

### scripts/box-destroy.sh
- [ ] Add retry logic
- [ ] Add failure notification
- [ ] Add logging
- [ ] Add forensic snapshot option

### scripts/box-invite.py
- [ ] Add token expiration options
- [ ] Add revocation support
- [ ] Add batch invite creation
- [ ] Add QR code generation

### tests/test_api.py
- [ ] Add integration tests
- [ ] Add security tests
- [ ] Add load tests
- [ ] Add chaos tests

### docker-compose.yml
- [ ] Add health checks
- [ ] Add resource limits
- [ ] Add logging drivers
- [ ] Add backup volumes

---

*Document generated by comprehensive codebase review*
