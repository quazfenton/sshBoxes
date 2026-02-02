#!/usr/bin/env bash
# Usage: ./box-destroy.sh <container_name> <metadata_file_optional>
set -euo pipefail

# Input validation
if [ $# -lt 1 ]; then
    echo "Error: Container name is required" >&2
    echo "Usage: $0 <container_name> [metadata_file]" >&2
    exit 1
fi

CONTAINER="$1"
META="${2:-}"

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed or not in PATH" >&2
    exit 1
fi

# Check if container exists before attempting to remove
CONTAINER_EXISTS=$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -Fc "$CONTAINER" || echo "0")

if [ "$CONTAINER_EXISTS" -gt 0 ]; then
    if ! docker rm -f "$CONTAINER" >/dev/null 2>&1; then
        echo "Warning: Failed to remove container '$CONTAINER'" >&2
    else
        echo "Successfully destroyed container: $CONTAINER"
    fi
else
    echo "Warning: Container '$CONTAINER' does not exist" >&2
fi

# Handle metadata file
if [ -n "$META" ]; then
    if [ -f "$META" ]; then
        if ! mv "$META" "${META}.destroyed" 2>/dev/null; then
            echo "Warning: Failed to rename metadata file '$META'" >&2
            # Try copying instead
            if cp "$META" "${META}.destroyed" 2>/dev/null; then
                rm -f "$META" 2>/dev/null || true
                echo "Renamed metadata file: $META -> ${META}.destroyed"
            else
                echo "Error: Could not rename or copy metadata file '$META'" >&2
            fi
        else
            echo "Renamed metadata file: $META -> ${META}.destroyed"
        fi
    else
        echo "Warning: Metadata file '$META' does not exist" >&2
    fi
fi

echo "Completed destruction process for: $CONTAINER"