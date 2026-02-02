#!/usr/bin/env bash
# Minimal HTTP endpoint to accept token exchange: POST /request with JSON {"token":"...","pubkey":"...","profile":"dev","ttl":300}
# Returns JSON with host/port/user/session_id or error.
set -euo pipefail
PORT="${GATEWAY_PORT:-8080}"
SECRET="${GATEWAY_SECRET:-replace-with-secret}"  # HMAC secret for token validation
PROVISIONER="./box-provision.sh"

# Use a proper approach to handle HTTP requests with netcat
while true; do
  # Accept a connection and handle it in a subshell to properly send response
  {
    # Read the HTTP request
    request=""
    while IFS= read -r line; do
      request="$request"$'\n'"$line"
      # Check for end of headers (empty line)
      if [[ "$line" == $'\r' ]] || [[ -z "$line" ]]; then
        break
      fi
    done

    # Read the body
    body=""
    while IFS= read -r line; do
      body="$body"$'\n'"$line"
    done

    # Extract token, pubkey, profile, ttl from body
    token=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])" 2>/dev/null)
    pubkey=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin)['pubkey'])" 2>/dev/null)
    profile=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('profile','dev'))" 2>/dev/null)
    ttl=$(echo "$body" | python3 -c "import sys,json; print(int(json.load(sys.stdin).get('ttl',1800)))" 2>/dev/null)

    # validate token (HMAC)
    # Use base64 to safely pass the secret to avoid shell injection
    SECRET_B64=$(echo -n "$SECRET" | base64 -w 0)
    valid=$(echo -n "$token" | python3 -c "
import sys, hashlib, hmac, base64
import json
secret_b64 = '$SECRET_B64'
secret = base64.b64decode(secret_b64).decode('utf-8')
token_data = sys.stdin.read().strip()
parts = token_data.split(':')
if len(parts) != 6:
    print('0')
else:
    profile, ttl_str, timestamp_str, recipient_hash, notes_hash, expected_sig = parts
    payload = ':'.join([profile, ttl_str, timestamp_str, recipient_hash, notes_hash])
    computed_sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    print('1' if hmac.compare_digest(computed_sig, expected_sig) else '0')
" 2>/dev/null)

    if [ "$valid" != "1" ] || [ -z "$token" ] || [ -z "$pubkey" ]; then
      echo -e "HTTP/1.1 403 Forbidden\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{\"error\":\"invalid token\"}"
    else
      SESSION_ID=$(date +%s%N)
      # call provisioner
      resp=$($PROVISIONER "$SESSION_ID" "$pubkey" "$profile" "$ttl" 2>/dev/null)
      if [ $? -eq 0 ]; then
        echo -e "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n$resp"
      else
        echo -e "HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{\"error\":\"provisioning failed\"}"
      fi
    fi
  } | nc -l -p "$PORT" -q 0
done