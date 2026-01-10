#!/usr/bin/env bash
# Usage: ./box-destroy.sh <container_name> <metadata_file_optional>
set -euo pipefail
CONTAINER="$1"
META="${2:-}"
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER}\\$"; then
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
fi
if [ -n "$META" ] && [ -f "$META" ]; then
  mv "$META" "${META}.destroyed"
fi
echo "Destroyed $CONTAINER"