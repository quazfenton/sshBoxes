#!/usr/bin/env bash
# Secure box provisioner with comprehensive input validation
# Usage: ./box-provision.sh <session_id> <pubkey> <profile> <ttl_seconds>
set -euo pipefail

# Strict error handling
set -o nounset   # Treat unset variables as errors
set -o pipefail  # Catch pipe failures

# ============================================================================
# Security Functions
# ============================================================================

# Validate that input contains only safe characters (alphanumeric, dash, underscore)
validate_safe_identifier() {
    local input="$1"
    local name="$2"
    local max_length="${3:-64}"
    
    if [[ -z "$input" ]]; then
        echo "Error: $name cannot be empty" >&2
        exit 1
    fi
    
    if [[ ${#input} -gt $max_length ]]; then
        echo "Error: $name exceeds maximum length of $max_length characters" >&2
        exit 1
    fi
    
    if [[ ! "$input" =~ ^[a-zA-Z0-9_-]+$ ]]; then
        echo "Error: $name contains invalid characters. Only alphanumeric, dashes, and underscores allowed." >&2
        exit 1
    fi
}

# Validate SSH public key format
validate_ssh_pubkey() {
    local pubkey="$1"
    
    if [[ -z "$pubkey" ]]; then
        echo "Error: SSH public key cannot be empty" >&2
        exit 1
    fi
    
    # Check for valid key type prefix
    if [[ ! "$pubkey" =~ ^(ssh-rsa\ |ssh-ed25519\ |ecdsa-sha2-nistp256\ |ecdsa-sha2-nistp384\ |ecdsa-sha2-nistp521\ |sk-ecdsa-sha2-nistp256@openssh.com\ |sk-ssh-ed25519@openssh.com\ ) ]]; then
        echo "Error: Invalid SSH public key format. Must start with ssh-rsa, ssh-ed25519, or ecdsa-*" >&2
        exit 1
    fi
    
    # Check minimum key length (base64 data should be at least 50 chars)
    local key_data="${pubkey#* }"
    if [[ ${#key_data} -lt 50 ]]; then
        echo "Error: SSH public key data too short" >&2
        exit 1
    fi
    
    # Check for dangerous options in key (command=, from=, etc.)
    if [[ "$pubkey" =~ (command=|from=|no-pty|no-port-forwarding|no-X11-forwarding|no-agent-forwarding) ]]; then
        echo "Error: SSH public key contains restricted options" >&2
        exit 1
    fi
}

# Validate TTL is a positive integer within range
validate_ttl() {
    local ttl="$1"
    local min_ttl="${2:-60}"
    local max_ttl="${3:-7200}"
    
    if [[ ! "$ttl" =~ ^[0-9]+$ ]]; then
        echo "Error: TTL must be a positive integer" >&2
        exit 1
    fi
    
    if [[ "$ttl" -lt "$min_ttl" ]]; then
        echo "Error: TTL must be at least $min_ttl seconds" >&2
        exit 1
    fi
    
    if [[ "$ttl" -gt "$max_ttl" ]]; then
        echo "Error: TTL must not exceed $max_ttl seconds" >&2
        exit 1
    fi
}

# Validate profile name
validate_profile() {
    local profile="$1"
    local allowed_profiles=("dev" "debug" "secure-shell" "privileged")
    
    for allowed in "${allowed_profiles[@]}"; do
        if [[ "$profile" == "$allowed" ]]; then
            return 0
        fi
    done
    
    echo "Error: Invalid profile '$profile'. Allowed profiles: ${allowed_profiles[*]}" >&2
    exit 1
}

# Cleanup function for error handling
cleanup_on_error() {
    local exit_code=$?
    if [[ $exit_code -ne 0 && -n "${SESSION_ID:-}" && -n "${CONTAINER_NAME:-}" ]]; then
        echo "Cleaning up after failed provisioning..." >&2
        docker rm -f "$CONTAINER_NAME" &>/dev/null || true
        rm -f "/tmp/${SESSION_ID}.json" 2>/dev/null || true
    fi
}

# ============================================================================
# Main Script
# ============================================================================

# Input validation
if [[ $# -lt 2 ]]; then
    echo "Error: Insufficient arguments" >&2
    echo "Usage: $0 <session_id> <pubkey> [profile] [ttl_seconds]" >&2
    exit 1
fi

SESSION_ID="$1"
PUBKEY="$2"
PROFILE="${3:-dev}"
TTL="${4:-1800}"  # default 30m

# Set up error trap for cleanup
trap cleanup_on_error EXIT

# Validate all inputs
validate_safe_identifier "$SESSION_ID" "SESSION_ID" 64
validate_ssh_pubkey "$PUBKEY"
validate_profile "$PROFILE"
validate_ttl "$TTL" 60 7200

# Configuration
IMAGE="ephemeral-box:latest"
CONTAINER_NAME="box_${SESSION_ID}"
RECORDINGS_DIR="${RECORDINGS_DIR:-/tmp/sshbox_recordings}"
ENABLE_RECORDING="${ENABLE_RECORDING:-false}"

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed or not in PATH" >&2
    exit 1
fi

# Check if Docker daemon is running
if ! docker info &>/dev/null; then
    echo "Error: Docker daemon is not running or not accessible" >&2
    exit 1
fi

# Check if required image exists
if ! docker images -q "$IMAGE" 2>/dev/null | grep -q .; then
    echo "Error: Required image '$IMAGE' does not exist" >&2
    echo "Please build the image first: docker build -t '$IMAGE' images/Dockerfile" >&2
    exit 1
fi

# Check if container with same name already exists
if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"; then
    echo "Error: Container with name '$CONTAINER_NAME' already exists" >&2
    exit 1
fi

# Create container with error handling
echo "Creating container $CONTAINER_NAME..." >&2
if ! container_id=$(docker run -d --name "$CONTAINER_NAME" -p 0:22 --rm "$IMAGE" sleep infinity 2>&1); then
    echo "Error: Failed to create container '$CONTAINER_NAME': $container_id" >&2
    exit 1
fi

# Wait for container to be ready
sleep 1

# Verify container is running
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"; then
    echo "Error: Container '$CONTAINER_NAME' failed to start" >&2
    docker logs "$CONTAINER_NAME" 2>&1 | head -20 >&2
    exit 1
fi

# Get dynamically mapped SSH port
SSH_PORT=$(docker port "$CONTAINER_NAME" 22 2>/dev/null | sed -E 's/.*:(.*)/\1/' || echo "")

# Validate SSH port
if [[ -z "$SSH_PORT" ]]; then
    echo "Error: Failed to get SSH port for container '$CONTAINER_NAME'" >&2
    exit 1
fi

if ! [[ "$SSH_PORT" =~ ^[0-9]+$ ]] || [[ "$SSH_PORT" -lt 1 || "$SSH_PORT" -gt 65535 ]]; then
    echo "Error: Invalid SSH port retrieved: '$SSH_PORT'" >&2
    exit 1
fi

# Inject user and SSH key
echo "Injecting SSH key..." >&2
if ! docker exec "$CONTAINER_NAME" mkdir -p /home/boxuser/.ssh 2>/dev/null; then
    echo "Error: Failed to create SSH directory in container" >&2
    exit 1
fi

# Write SSH key securely (avoid shell injection by using heredoc)
if ! docker exec -i "$CONTAINER_NAME" tee /home/boxuser/.ssh/authorized_keys > /dev/null <<< "$PUBKEY"; then
    echo "Error: Failed to inject SSH key into container" >&2
    exit 1
fi

# Set proper permissions
if ! docker exec "$CONTAINER_NAME" chown -R boxuser:boxuser /home/boxuser/.ssh 2>/dev/null || \
   ! docker exec "$CONTAINER_NAME" chmod 700 /home/boxuser/.ssh 2>/dev/null || \
   ! docker exec "$CONTAINER_NAME" chmod 600 /home/boxuser/.ssh/authorized_keys 2>/dev/null; then
    echo "Error: Failed to set proper permissions for SSH keys" >&2
    exit 1
fi

# Setup session recording if enabled
if [[ "$ENABLE_RECORDING" == "true" ]]; then
    echo "Setting up session recording..." >&2
    
    if ! docker exec "$CONTAINER_NAME" mkdir -p "$RECORDINGS_DIR" 2>/dev/null; then
        echo "Warning: Failed to create recordings directory in container" >&2
    else
        # Create a wrapper script that records the session
        if docker exec -i "$CONTAINER_NAME" tee /usr/local/bin/recorded-shell.sh > /dev/null << 'EOF'
#!/bin/bash
SHELL_RECORDING_DIR="${RECORDINGS_DIR:-/tmp/sshbox_recordings}"
SESSION_ID="$1"
mkdir -p "$SHELL_RECORDING_DIR"
if command -v asciinema &> /dev/null; then
    asciinema rec -c "bash" "$SHELL_RECORDING_DIR/session-$SESSION_ID.cast" --title "SSH Session $SESSION_ID" --overwrite
else
    script -f "$SHELL_RECORDING_DIR/session-$SESSION_ID.typescript" -c "bash"
fi
EOF
            echo "Recording script created" >&2
            
            if ! docker exec "$CONTAINER_NAME" chmod +x /usr/local/bin/recorded-shell.sh 2>/dev/null; then
                echo "Warning: Failed to make recording script executable" >&2
            else
                # Configure SSH to use the recording wrapper
                if ! docker exec "$CONTAINER_NAME" sh -c "echo 'ForceCommand /usr/local/bin/recorded-shell.sh $SESSION_ID' >> /etc/ssh/sshd_config" 2>/dev/null; then
                    echo "Warning: Failed to configure SSH for recording" >&2
                else
                    if ! docker exec "$CONTAINER_NAME" pkill -HUP sshd 2>/dev/null; then
                        echo "Warning: Failed to reload SSH daemon for recording" >&2
                    else
                        echo "Session recording configured" >&2
                    fi
                fi
            fi
        fi
    fi
fi

# Get host IP address
get_host_ip() {
    # Try different methods to get host IP
    if command -v hostname &> /dev/null; then
        local ip=$(hostname -I 2>/dev/null | awk '{print $1}' | head -n1)
        if [[ -n "$ip" ]]; then
            echo "$ip"
            return
        fi
    fi
    
    # Fallback to localhost
    echo "127.0.0.1"
}

HOST_IP=$(get_host_ip)

# Record metadata securely
METADATA_FILE="/tmp/${SESSION_ID}.json"
if ! cat > "$METADATA_FILE" << EOF
{
  "session_id": "$SESSION_ID",
  "container": "$CONTAINER_NAME",
  "ssh_port": $SSH_PORT,
  "ttl": $TTL,
  "profile": "$PROFILE",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "host_ip": "$HOST_IP"
}
EOF
then
    echo "Error: Failed to write metadata file" >&2
    exit 1
fi

# Set restrictive permissions on metadata file
chmod 600 "$METADATA_FILE"

# Schedule destruction (background process)
(
    sleep "$TTL"
    # Check if container still exists before destroying
    if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"; then
        ./box-destroy.sh "$CONTAINER_NAME" 2>/dev/null || true
    fi
) & disown

# Output connection info as JSON
echo "{\"host\":\"$HOST_IP\",\"port\":$SSH_PORT,\"user\":\"boxuser\",\"session_id\":\"$SESSION_ID\",\"profile\":\"$PROFILE\",\"ttl\":$TTL}"

# Clear trap on success
trap - EXIT

exit 0