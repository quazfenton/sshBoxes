#!/usr/bin/env bash
# Usage: ./box-provision.sh <session_id> <pubkey> <profile> <ttl_seconds>
set -euo pipefail

# Input validation
if [ $# -lt 2 ]; then
    echo "Error: Insufficient arguments" >&2
    echo "Usage: $0 <session_id> <pubkey> [profile] [ttl_seconds]" >&2
    exit 1
fi

SESSION_ID="$1"
PUBKEY="$2"
PROFILE="${3:-dev}"
TTL="${4:-1800}"  # default 30m

# Validate TTL is a positive integer
if ! [[ "$TTL" =~ ^[0-9]+$ ]] || [ "$TTL" -le 0 ]; then
    echo "Error: TTL must be a positive integer" >&2
    exit 1
fi

IMAGE="ephemeral-box:latest"  # ensure image exists with sshd installed
CONTAINER_NAME="box_${SESSION_ID}"
RECORDINGS_DIR="${RECORDINGS_DIR:-/tmp/sshbox_recordings}"

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed or not in PATH" >&2
    exit 1
fi

# Check if required image exists
if ! docker images -q "$IMAGE" | grep -q .; then
    echo "Error: Required image '$IMAGE' does not exist" >&2
    echo "Please build the image first: docker build -t '$IMAGE' images/Dockerfile" >&2
    exit 1
fi

# Create container with error handling
if ! docker run -d --name "$CONTAINER_NAME" -p 0:22 --rm "$IMAGE" sleep infinity > /dev/null 2>&1; then
    echo "Error: Failed to create container '$CONTAINER_NAME'" >&2
    exit 1
fi

# get dynamically mapped ssh port
SSH_PORT=$(docker port "$CONTAINER_NAME" 22 2>/dev/null | sed -E 's/.*:(.*)/\1/' || {
    echo "Error: Failed to get SSH port for container '$CONTAINER_NAME'" >&2
    docker rm -f "$CONTAINER_NAME" > /dev/null 2>&1 || true
    exit 1
})

# Validate SSH port
if [ -z "$SSH_PORT" ] || ! [[ "$SSH_PORT" =~ ^[0-9]+$ ]]; then
    echo "Error: Invalid SSH port retrieved: '$SSH_PORT'" >&2
    docker rm -f "$CONTAINER_NAME" > /dev/null 2>&1 || true
    exit 1
fi

# inject user and ssh key (assume user 'boxuser' exists in image)
if ! docker exec "$CONTAINER_NAME" mkdir -p /home/boxuser/.ssh 2>/dev/null; then
    echo "Error: Failed to create SSH directory in container" >&2
    docker rm -f "$CONTAINER_NAME" > /dev/null 2>&1 || true
    exit 1
fi

if ! docker exec -i "$CONTAINER_NAME" bash -lc "cat > /home/boxuser/.ssh/authorized_keys" <<< "$PUBKEY" 2>/dev/null; then
    echo "Error: Failed to inject SSH key into container" >&2
    docker rm -f "$CONTAINER_NAME" > /dev/null 2>&1 || true
    exit 1
fi

if ! docker exec "$CONTAINER_NAME" chown -R boxuser:boxuser /home/boxuser/.ssh 2>/dev/null || \
   ! docker exec "$CONTAINER_NAME" chmod 700 /home/boxuser/.ssh 2>/dev/null || \
   ! docker exec "$CONTAINER_NAME" chmod 600 /home/boxuser/.ssh/authorized_keys 2>/dev/null; then
    echo "Error: Failed to set proper permissions for SSH keys" >&2
    docker rm -f "$CONTAINER_NAME" > /dev/null 2>&1 || true
    exit 1
fi

# Setup session recording if enabled
if [ "${ENABLE_RECORDING:-false}" = "true" ]; then
    if ! docker exec "$CONTAINER_NAME" mkdir -p "$RECORDINGS_DIR" 2>/dev/null; then
        echo "Warning: Failed to create recordings directory in container" >&2
    else
        # Create a wrapper script that records the session
        if ! docker exec -i "$CONTAINER_NAME" sh -c 'cat > /usr/local/bin/recorded-shell.sh' << 'EOF' 2>/dev/null; then
#!/bin/bash
SHELL_RECORDING_DIR="${RECORDINGS_DIR:-/tmp/sshbox_recordings}"
SESSION_ID="$1"
mkdir -p "$SHELL_RECORDING_DIR"
asciinema rec -c "bash" "$SHELL_RECORDING_DIR/session-$SESSION_ID.cast" --title "SSH Session $SESSION_ID" --overwrite
EOF
            echo "Warning: Failed to create recording script in container" >&2
        else
            if ! docker exec "$CONTAINER_NAME" chmod +x /usr/local/bin/recorded-shell.sh 2>/dev/null; then
                echo "Warning: Failed to make recording script executable" >&2
            else
                # Configure SSH to use the recording wrapper
                if ! docker exec "$CONTAINER_NAME" sh -c "echo 'ForceCommand /usr/local/bin/recorded-shell.sh $SESSION_ID' >> /etc/ssh/sshd_config" 2>/dev/null; then
                    echo "Warning: Failed to configure SSH for recording" >&2
                else
                    if ! docker exec "$CONTAINER_NAME" pkill -HUP sshd 2>/dev/null; then
                        echo "Warning: Failed to reload SSH daemon for recording" >&2
                    fi
                fi
            fi
        fi
    fi
fi

# record metadata
METADATA_FILE="/tmp/${SESSION_ID}.json"
if ! echo "{\"session_id\":\"$SESSION_ID\",\"container\":\"$CONTAINER_NAME\",\"ssh_port\":$SSH_PORT,\"ttl\":$TTL,\"profile\":\"$PROFILE\",\"created_at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$METADATA_FILE" 2>/dev/null; then
    echo "Error: Failed to write metadata file" >&2
    docker rm -f "$CONTAINER_NAME" > /dev/null 2>&1 || true
    exit 1
fi

# schedule destroy
( sleep "$TTL"; ./box-destroy.sh "$CONTAINER_NAME" "$METADATA_FILE" 2>/dev/null || true ) & disown

# output connect info
echo "{\"host\":\"$(hostname -I | awk '{print $1}' | head -n1)\",\"port\":$SSH_PORT,\"user\":\"boxuser\",\"session_id\":\"$SESSION_ID\"}"