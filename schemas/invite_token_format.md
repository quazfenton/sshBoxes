# Invite Token Format

The invite token follows an HMAC-based format to ensure authenticity and integrity:

## Format
```
"profile:ttl:timestamp:hmac_signature"
```

## Components
- `profile`: The requested box profile (e.g., "dev", "debug", "secure-shell")
- `ttl`: Time-to-live in seconds (e.g., 600 for 10 minutes)
- `timestamp`: Unix timestamp when token was created (seconds since epoch)
- `hmac_signature`: SHA-256 HMAC signature of "profile:ttl:timestamp" using shared secret

## Example
```
"dev:600:1678886400:a3f4c2e1d5b6..."
```

## Validation
To validate a token:
1. Split by ':'
2. Extract first three components (profile, ttl, timestamp)
3. Recalculate HMAC using shared secret
4. Compare signatures using constant-time comparison