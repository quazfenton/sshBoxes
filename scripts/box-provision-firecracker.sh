#!/usr/bin/env bash
# Firecracker-based box provisioner
# Usage: ./box-provision-firecracker.sh <session_id> <pubkey> <profile> <ttl_seconds>

set -euo pipefail
umask 077

if (( $# < 2 || $# > 4 )); then
  echo "Usage: $0 <session_id> <pubkey> [profile] [ttl_seconds]" >&2
  exit 2
fi

SESSION_ID="$1"

# Validate SESSION_ID contains only safe characters
if [[ ! "$SESSION_ID" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo "Error: Invalid SESSION_ID. Must contain only alphanumeric characters, dashes, or underscores." >&2
  exit 1
fi
PUBKEY="$2"
PROFILE="${3:-dev}"
TTL="${4:-1800}"  # default 30m

if [[ ! "$TTL" =~ ^[0-9]+$ ]]; then
  echo "Error: ttl_seconds must be an integer (got: $TTL)" >&2
  exit 2
fi
# Configuration
FIRECRACKER_SOCKET="/tmp/firecracker-${SESSION_ID}.socket"
KERNEL_IMAGE="${KERNEL_IMAGE:-/var/lib/firecracker/kernel/vmlinux}"
ROOTFS_IMAGE="${ROOTFS_IMAGE:-/var/lib/firecracker/rootfs/rootfs.ext4}"
VM_MEM_SIZE="${VM_MEM_SIZE:-2048}"  # MB
VM_VCPU_COUNT="${VM_VCPU_COUNT:-2}"

# Create temporary directory for this session
SESSION_DIR="/tmp/firecracker-session-${SESSION_ID}"
mkdir -p "$SESSION_DIR"

# Create a copy of the rootfs for this session (to maintain isolation)
SESSION_ROOTFS="${SESSION_DIR}/rootfs.ext4"
cp "$ROOTFS_IMAGE" "$SESSION_ROOTFS"

# Insert the SSH public key into the rootfs
# Mount the filesystem temporarily
MOUNT_POINT="${SESSION_DIR}/mnt"
mkdir -p "$MOUNT_POINT"
mounted=0
cleanup() {
  if (( mounted )); then sudo umount "$MOUNT_POINT" || true; fi
}
trap cleanup EXIT

sudo mount -o loop "$SESSION_ROOTFS" "$MOUNT_POINT"
mounted=1

# Create SSH directory and add the public key
sudo mkdir -p "$MOUNT_POINT/home/boxuser/.ssh"
echo "$PUBKEY" | sudo tee "$MOUNT_POINT/home/boxuser/.ssh/authorized_keys" >/dev/null
sudo chown -R 1000:1000 "$MOUNT_POINT/home/boxuser/.ssh"
sudo chmod 700 "$MOUNT_POINT/home/boxuser/.ssh"
sudo chmod 600 "$MOUNT_POINT/home/boxuser/.ssh/authorized_keys"

# Consider *not* adding to root; if you must, at least gate behind an env flag.

# Unmount
sudo umount "$MOUNT_POINT"
mounted=0
# Prepare Firecracker configuration
cat > "${SESSION_DIR}/config.json" <<EOF
{
  "boot-source": {
    "kernel_image_path": "$KERNEL_IMAGE",
    "boot_args": "console=ttyS0 noapic reboot=k panic=1 pci=off nomodules ip=dhcp"
  },
  "drives": [
    {
      "drive_id": "rootfs",
      "path_on_host": "$SESSION_ROOTFS",
      "is_root_device": true,
      "is_read_only": false
    }
  ],
  "machine-config": {
    "vcpu_count": $VM_VCPU_COUNT,
    "mem_size_mib": $VM_MEM_SIZE,
    "ht_enabled": false
  },
  "network-interfaces": [
    {
      "iface_id": "eth0",
      "guest_mac": "AA:FC:00:00:00:01",
      "host_dev_name": "tap0"
    }
  ]
}
EOF

# Start Firecracker VM
firecracker --api-sock "$FIRECRACKER_SOCKET" --config-file "${SESSION_DIR}/config.json" &
FIRECRACKER_PID=$!

# Wait for VM to boot and discover IP address
echo "Waiting for VM to boot..." >&2
sleep 10

# Method 1: Try to get IP from ARP table
VM_IP=""
VM_MAC="AA:FC:00:00:00:01"

# Scan ARP table for VM MAC address
if command -v arp &> /dev/null; then
    for i in {1..30}; do
        VM_IP=$(arp -an 2>/dev/null | grep -i "$VM_MAC" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -1)
        if [[ -n "$VM_IP" ]]; then
            echo "Discovered VM IP from ARP: $VM_IP" >&2
            break
        fi
        sleep 2
    done
fi

# Method 2: Try DHCP lease file
if [[ -z "$VM_IP" ]]; then
    for lease_file in /var/lib/misc/dnsmasq.leases /var/lib/dnsmasq/dnsmasq.leases /tmp/dnsmasq.leases; do
        if [[ -f "$lease_file" ]]; then
            VM_IP=$(grep -i "$VM_MAC" "$lease_file" 2>/dev/null | awk '{print $3}' | head -1)
            if [[ -n "$VM_IP" ]]; then
                echo "Discovered VM IP from DHCP: $VM_IP" >&2
                break
            fi
        fi
    done
fi

# Fallback: Use default IP with warning
if [[ -z "$VM_IP" ]]; then
    echo "WARNING: Could not discover VM IP, using default 172.16.0.10" >&2
    echo "Configure DHCP lease monitoring for better accuracy." >&2
    VM_IP="172.16.0.10"
fi

SSH_PORT=22

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
META_PATH="/tmp/${SESSION_ID}.json"
if [[ -L "$META_PATH" ]]; then echo "Error: refusing to write symlink: $META_PATH" >&2; exit 1; fi

# record metadata
jq -n \
  --arg session_id "$SESSION_ID" \
  --arg vm_socket "$FIRECRACKER_SOCKET" \
  --arg vm_ip "$VM_IP" \
  --arg profile "$PROFILE" \
  --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --argjson ssh_port "$SSH_PORT" \
  --argjson ttl "$TTL" \
  '{session_id:$session_id,vm_socket:$vm_socket,vm_ip:$vm_ip,ssh_port:$ssh_port,ttl:$ttl,profile:$profile,created_at:$created_at}' \
  > "$META_PATH"

# schedule destroy
( sleep "$TTL"; "$SCRIPT_DIR/box-destroy-firecracker.sh" "$SESSION_ID" "$META_PATH" ) & disown

# output connect info
echo "{\"host\":\"$VM_IP\",\"port\":$SSH_PORT,\"user\":\"boxuser\",\"session_id\":\"$SESSION_ID\"}"