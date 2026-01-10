#!/usr/bin/env bash
# Minimal HTTP endpoint to accept token exchange: POST /request with JSON {"token":"...","pubkey":"...","profile":"dev","ttl":300}
# Returns JSON with host/port/user/session_id or error.
set -euo pipefail
PORT="${GATEWAY_PORT:-8080}"
SECRET="${GATEWAY_SECRET:-replace-with-secret}"  # HMAC secret for token validation
PROVISIONER="./box-provision.sh"

# very small loop using nc to accept one request at a time (for demo only)
while true; do
  # listen for one HTTP request
  req=$(nc -l -p "$PORT" -q 1)
  body=$(echo "$req" | sed -n '/^\\r$/,$p' | sed '1d')
  token=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
  pubkey=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin)['pubkey'])")
  profile=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('profile','dev'))")
  ttl=$(echo "$body" | python3 -c "import sys,json; print(int(json.load(sys.stdin).get('ttl',1800)))")
  # validate token (HMAC)
  valid=$(echo -n "$token" | python3 -c "import sys,hashlib,hmac; s='$SECRET'; t=sys.stdin.read().strip(); print('1' if hmac.compare_digest(hmac.new(s.encode(), t.encode(), hashlib.sha256).hexdigest(), t.split(':')[-1]) else '0')")
  if [ "$valid" != "1" ]; then
    echo -e "HTTP/1.1 403 Forbidden\\r\\nContent-Type: application/json\\r\\n\\r\\n{\\"error\\":\\"invalid token\\"}"
    continue
  fi
  SESSION_ID=$(date +%s%N)
  # call provisioner
  resp=$($PROVISIONER "$SESSION_ID" "$pubkey" "$profile" "$ttl")
  echo -e "HTTP/1.1 200 OK\\r\\nContent-Type: application/json\\r\\n\\r\\n$resp"
done