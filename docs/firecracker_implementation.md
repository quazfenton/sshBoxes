# Firecracker MicroVM Implementation for sshBox

This document describes the implementation of Firecracker microVMs for the sshBox system, providing stronger isolation than containers.

## Architecture Overview

The Firecracker implementation follows the same interface as the container-based implementation but provides stronger security isolation through microVMs:

- Each sshBox runs in its own lightweight virtual machine
- VMs are provisioned with Firecracker, Amazon's VMM for serverless computing
- Each VM gets its own root filesystem copy for complete isolation
- SSH access is provided through the VM's network interface

## Prerequisites

Before using the Firecracker implementation, ensure the following are installed and configured:

1. **Firecracker**:
   ```bash
   # Install firecracker binary
   wget https://github.com/firecracker-microvm/firecracker/releases/download/v1.5.0/firecracker-v1.5.0-x86_64
   sudo install firecracker-v1.5.0-x86_64 /usr/local/bin/firecracker
   ```

2. **Kernel Image**:
   - A minimal Linux kernel supporting initramfs and necessary drivers
   - Place at `/var/lib/firecracker/kernel/vmlinux` or set `KERNEL_IMAGE` env var

3. **Root Filesystem**:
   - A minimal rootfs with SSH server and basic tools
   - Place at `/var/lib/firecracker/rootfs/rootfs.ext4` or set `ROOTFS_IMAGE` env var

4. **System Configuration**:
   ```bash
   # Enable nested virtualization (if running on VM)
   # Check: cat /proc/cpuinfo | grep vmx
   
   # Increase memlock limits for Firecracker
   echo "* soft memlock unlimited" | sudo tee -a /etc/security/limits.conf
   echo "* hard memlock unlimited" | sudo tee -a /etc/security/limits.conf
   ```

## Configuration

Environment variables to customize the Firecracker implementation:

- `KERNEL_IMAGE`: Path to the kernel image (default: `/var/lib/firecracker/kernel/vmlinux`)
- `ROOTFS_IMAGE`: Path to the root filesystem image (default: `/var/lib/firecracker/rootfs/rootfs.ext4`)
- `VM_MEM_SIZE`: Memory size in MB for each VM (default: 2048)
- `VM_VCPU_COUNT`: Number of vCPUs per VM (default: 2)

## Root Filesystem Creation

To create a minimal root filesystem for the VMs:

```bash
# Create a sparse file for the rootfs
dd if=/dev/zero of=rootfs.ext4 bs=1M count=2048  # 2GB
mkfs.ext4 rootfs.ext4

# Mount and install minimal system
mkdir mnt
sudo mount -o loop rootfs.ext4 mnt

# Install minimal Ubuntu system (using debootstrap)
sudo debootstrap --arch amd64 focal mnt http://archive.ubuntu.com/ubuntu/

# Install SSH server and other essentials
sudo chroot mnt apt update
sudo chroot mnt apt install -y openssh-server sudo cloud-init

# Create boxuser
sudo chroot mnt useradd -m -s /bin/bash boxuser
echo "boxuser ALL=(ALL) NOPASSWD:ALL" | sudo tee mnt/etc/sudoers.d/boxuser > /dev/null

# Configure SSH
sudo chroot mnt sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config
sudo chroot mnt sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config

# Unmount
sudo umount mnt
```

## Network Configuration

For networking, you can use TAP interfaces. Example setup:

```bash
# Create a bridge
sudo ip link add br0 type bridge
sudo ip addr add 172.16.0.1/24 dev br0
sudo ip link set br0 up

# Create TAP interface
sudo ip tuntap add dev tap0 mode tap
sudo ip link set tap0 master br0
sudo ip link set tap0 up
sudo ip addr add 172.16.0.2/24 dev tap0
```

## Scripts

The implementation includes two main scripts:

1. `box-provision-firecracker.sh` - Creates a new Firecracker VM with the provided SSH key
2. `box-destroy-firecracker.sh` - Terminates and cleans up a Firecracker VM

## Integration with sshBox System

To use the Firecracker implementation instead of containers:

1. Update the provisioner path in your configuration
2. Set the required environment variables
3. Ensure all prerequisites are met

## Security Benefits

Compared to the container implementation, Firecracker provides:

- Stronger isolation through hardware virtualization
- Separate kernel instance per VM
- Better protection against container escapes
- More predictable resource allocation

## Performance Characteristics

- VM startup time: ~1-3 seconds (after kernel and rootfs are loaded)
- Memory overhead: ~5MB per VM plus configured amount
- CPU overhead: Minimal (typically <1%)
- Boot time can be reduced further with techniques like kernel preloading

## Limitations

- Higher resource overhead compared to containers
- Requires more system configuration
- May not work in all hosting environments (needs KVM support)
- More complex debugging due to VM abstraction layer