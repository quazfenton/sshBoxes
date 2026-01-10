#!/usr/bin/env bash
# Firecracker-based box destroyer
# Usage: ./box-destroy-firecracker.sh <session_id> <metadata_file_optional>

set -euo pipefail

SESSION_ID="$1"
META="${2:-}"

# Determine socket path
FIRECRACKER_SOCKET="/tmp/firecracker-${SESSION_ID}.socket"

# Stop the Firecracker VM gracefully if possible
if [ -S "$FIRECRACKER_SOCKET" ]; then
    # Send shutdown command to Firecracker
    curl --unix-socket "$FIRECRACKER_SOCKET" -i \
         -X PUT "http://localhost/actions" \
         -H "Accept: application/json" \
         -H "Content-Type: application/json" \
         -d '{"action_type": "SendCtrlAltDel"}'
    
    # Wait a few seconds for graceful shutdown
    sleep 5
    
    # Kill the process if it's still running
    FC_PID=$(pgrep -f "firecracker.*$SESSION_ID" || true)
    if [ -n "$FC_PID" ]; then
        kill -9 "$FC_PID" 2>/dev/null || true
    fi
fi

# Clean up session directory
SESSION_DIR="/tmp/firecracker-session-${SESSION_ID}"
if [ -d "$SESSION_DIR" ]; then
    # Unmount if still mounted
    MOUNT_POINT="${SESSION_DIR}/mnt"
    if mountpoint -q "$MOUNT_POINT"; then
        sudo umount "$MOUNT_POINT" 2>/dev/null || true
    fi
    
    # Remove session directory
    rm -rf "$SESSION_DIR"
fi

# Clean up socket file
rm -f "$FIRECRACKER_SOCKET"

# Handle metadata file
if [ -n "$META" ] && [ -f "$META" ]; then
    mv "$META" "${META}.destroyed"
fi

echo "Destroyed Firecracker VM for session $SESSION_ID"