# ssh-gateway-proxy.sh Deprecation Notice

**Deprecation Date**: 2026-03-05  
**Removal Date**: 2026-04-01 (30 days from deprecation)  
**Severity**: HIGH - Security vulnerabilities

## Security Issues

This script has been deprecated due to the following security vulnerabilities:

1. **Command Injection Risk**: Unquoted variable expansion allows shell injection
2. **Unsafe Python Execution**: User input passed directly to Python via stdin
3. **Insecure Token Validation**: String interpolation in HMAC validation
4. **Unvalidated Arguments**: Provisioner called without proper input sanitization

## Migration Guide

### Step 1: Start the FastAPI Gateway

```bash
# Set required environment variables
export SSHBOX_SECURITY_GATEWAY_SECRET="your-secure-secret-at-least-32-chars"
export SSHBOX_DB_SQLITE_PATH="/var/lib/sshbox/sessions.db"
export SSHBOX_STORAGE_RECORDINGS_DIR="/var/lib/sshbox/recordings"

# Start the FastAPI gateway
cd /path/to/sshBoxes
python -m uvicorn api.gateway_fastapi:app --host 0.0.0.0 --port 8080
```

### Step 2: Update Client Requests

**Old (bash gateway)**:
```bash
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"token":"...","pubkey":"...","profile":"dev","ttl":300}'
```

**New (FastAPI gateway)**:
```bash
curl -X POST http://localhost:8080/api/session/request \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"pubkey":"ssh-rsa AAAA...","profile":"dev","ttl":300}'
```

### Step 3: Update Provisioning Flow

The FastAPI gateway provides these endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/session/request` | POST | Request new session |
| `/api/session/destroy` | POST | Destroy session |
| `/api/sessions` | GET | List all sessions |
| `/api/health` | GET | Health check |

### Step 4: Remove Old Script

After migration is complete:

```bash
# Remove the deprecated script
rm scripts/ssh-gateway-proxy.sh

# Update any documentation referencing the old script
# Update CI/CD pipelines
```

## FastAPI Gateway Features

- ✅ Input validation with Pydantic models
- ✅ Constant-time HMAC token validation
- ✅ SQL injection prevention
- ✅ Path traversal protection
- ✅ Rate limiting
- ✅ CORS configuration
- ✅ Structured logging
- ✅ OpenAPI documentation

## Verification

After migration, verify the gateway is working:

```bash
# Health check
curl http://localhost:8080/api/health

# Should return: {"status": "healthy", "timestamp": "..."}
```

## Support

For migration issues, see:
- `api/gateway_fastapi.py` - Gateway implementation
- `docs/COMPLETE_IMPLEMENTATION.md` - Full documentation
- `tests/test_security.py` - Security test suite
