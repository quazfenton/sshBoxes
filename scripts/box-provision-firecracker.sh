#!/usr/bin/env bash
# Firecracker-based box provisioner
# Usage: ./box-provision-firecracker.sh <session_id> <pubkey> <profile> <ttl_seconds>

set -euo pipefail

SESSION_ID="$1"
PUBKEY="$2"
PROFILE="${3:-dev}"
TTL="${4:-1800}"  # default 30m

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
sudo mount -o loop "$SESSION_ROOTFS" "$MOUNT_POINT"

# Create SSH directory and add the public key
sudo mkdir -p "$MOUNT_POINT/home/boxuser/.ssh"
echo "$PUBKEY" | sudo tee "$MOUNT_POINT/home/boxuser/.ssh/authorized_keys"
sudo chown -R 1000:1000 "$MOUNT_POINT/home/boxuser/.ssh"
sudo chmod 700 "$MOUNT_POINT/home/boxuser/.ssh"
sudo chmod 600 "$MOUNT_POINT/home/boxuser/.ssh/authorized_keys"

# Also add to root user for flexibility
sudo mkdir -p "$MOUNT_POINT/root/.ssh"
echo "$PUBKEY" | sudo tee "$MOUNT_POINT/root/.ssh/authorized_keys"
sudo chmod 700 "$MOUNT_POINT/root/.ssh"
sudo chmod 600 "$MOUNT_POINT/root/.ssh/authorized_keys"

# Unmount
sudo umount "$MOUNT_POINT"

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

# Wait a moment for the VM to start
sleep 5

# Get the IP address assigned to the VM (this would need to be determined based on your network setup)
# For now, we'll assume it gets an IP from DHCP or you have a mapping system
VM_IP="172.16.0.10"  # This would be determined dynamically in a real implementation
SSH_PORT=22

# record metadata
echo "{\"session_id\":\"$SESSION_ID\",\"vm_socket\":\"$FIRECRACKER_SOCKET\",\"vm_ip\":\"$VM_IP\",\"ssh_port\":$SSH_PORT,\"ttl\":$TTL,\"profile\":\"$PROFILE\",\"created_at\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "/tmp/${SESSION_ID}.json"

# schedule destroy
( sleep "$TTL"; ./box-destroy-firecracker.sh "$SESSION_ID" "/tmp/${SESSION_ID}.json" ) & disown

# output connect info
echo "{\"host\":\"$VM_IP\",\"port\":$SSH_PORT,\"user\":\"boxuser\",\"session_id\":\"$SESSION_ID\"}"