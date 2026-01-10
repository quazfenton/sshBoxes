#!/usr/bin/env bash
# Usage: ./box-provision.sh <session_id> <pubkey> <profile> <ttl_seconds>
set -euo pipefail
SESSION_ID="$1"
PUBKEY="$2"
PROFILE="${3:-dev}"
TTL="${4:-1800}"  # default 30m
IMAGE="ephemeral-box:latest"  # ensure image exists with sshd installed
CONTAINER_NAME="box_${SESSION_ID}"
RECORDINGS_DIR="${RECORDINGS_DIR:-/tmp/sshbox_recordings}"

# Create container
docker run -d --name "$CONTAINER_NAME" -p 0:22 --rm "$IMAGE" sleep infinity

# get dynamically mapped ssh port
SSH_PORT=$(docker port "$CONTAINER_NAME" 22 | sed -E 's/.*:(.*)/\\1/')

# inject user and ssh key (assume user 'boxuser' exists in image)
docker exec "$CONTAINER_NAME" mkdir -p /home/boxuser/.ssh
docker exec -i "$CONTAINER_NAME" bash -lc "cat > /home/boxuser/.ssh/authorized_keys" <<< "$PUBKEY"
docker exec "$CONTAINER_NAME" chown -R boxuser:boxuser /home/boxuser/.ssh && chmod 700 /home/boxuser/.ssh && chmod 600 /home/boxuser/.ssh/authorized_keys

# Setup session recording if enabled
if [ "${ENABLE_RECORDING:-false}" = "true" ]; then
    docker exec "$CONTAINER_NAME" mkdir -p "$RECORDINGS_DIR"
    # Create a wrapper script that records the session
    docker exec -i "$CONTAINER_NAME" sh -c 'cat > /usr/local/bin/recorded-shell.sh' << 'EOF'
#!/bin/bash
SHELL_RECORDING_DIR="${RECORDINGS_DIR:-/tmp/sshbox_recordings}"
SESSION_ID="$1"
mkdir -p "$SHELL_RECORDING_DIR"
asciinema rec -c "bash" "$SHELL_RECORDING_DIR/session-$SESSION_ID.cast" --title "SSH Session $SESSION_ID" --overwrite
EOF
    docker exec "$CONTAINER_NAME" chmod +x /usr/local/bin/recorded-shell.sh
    # Configure SSH to use the recording wrapper
    docker exec "$CONTAINER_NAME" sh -c "echo 'ForceCommand /usr/local/bin/recorded-shell.sh $SESSION_ID' >> /etc/ssh/sshd_config"
    docker exec "$CONTAINER_NAME" pkill -HUP sshd
fi

# record metadata
echo "{\\"session_id\\":\\"$SESSION_ID\\",\\"container\\":\\"$CONTAINER_NAME\\",\\"ssh_port\\":$SSH_PORT,\\"ttl\\":$TTL,\\"profile\\":\\"$PROFILE\\",\\"created_at\\":\\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\\"}" > "/tmp/${SESSION_ID}.json"

# schedule destroy
( sleep "$TTL"; ./box-destroy.sh "$CONTAINER_NAME" "/tmp/${SESSION_ID}.json" ) & disown

# output connect info
echo "{\\"host\\":\\"$(hostname -I | awk '{print $1}')\\",\\"port\\":$SSH_PORT,\\"user\\":\\"boxuser\\",\\"session_id\\":\\"$SESSION_ID\\"}"