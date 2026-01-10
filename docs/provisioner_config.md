# Provisioner Configuration for sshBox

This configuration allows switching between different provisioner backends.

## Container-based Provisioner (Default)

For development and testing, uses Docker containers:

```yaml
provisioner:
  type: "container"  # Options: container, firecracker
  container:
    image: "sshbox-base:latest"
    runtime: "docker"  # or "podman"
    enable_recording: true
    recordings_dir: "/tmp/sshbox_recordings"
    default_resources:
      cpu_limit: "1"
      memory_limit: "2Gi"
    network_policy:
      internet_access: true
      egress_allowed:
        - "github.com:443"
        - "pypi.org:443"
        - "registry.npmjs.org:443"
```

## Firecracker MicroVM Provisioner

For production environments requiring stronger isolation:

```yaml
provisioner:
  type: "firecracker"
  firecracker:
    kernel_image: "/var/lib/firecracker/kernel/vmlinux"
    rootfs_image: "/var/lib/firecracker/rootfs/rootfs.ext4"
    default_vm_config:
      mem_size_mib: 2048
      vcpu_count: 2
    enable_recording: true
    recordings_dir: "/tmp/sshbox_recordings"
    network_interface: "tap0"
    socket_dir: "/var/run/firecracker"
```

## Profile-specific Configuration

Different profiles can have different configurations:

```yaml
profiles:
  dev:
    provisioner_type: "container"  # Use containers for dev
    container:
      image: "sshbox-dev:latest"
      resources:
        cpu_limit: "1"
        memory_limit: "2Gi"
      ttl_default: 1800
      ttl_max: 3600
  
  debug:
    provisioner_type: "container"
    container:
      image: "sshbox-debug:latest"
      resources:
        cpu_limit: "2"
        memory_limit: "4Gi"
      ttl_default: 3600
      ttl_max: 7200
  
  secure-shell:
    provisioner_type: "firecracker"  # Use VMs for secure shells
    firecracker:
      rootfs_image: "/var/lib/firecracker/rootfs/secure-rootfs.ext4"
      resources:
        mem_size_mib: 1024
        vcpu_count: 1
      ttl_default: 600
      ttl_max: 1800
  
  privileged:
    provisioner_type: "firecracker"
    firecracker:
      rootfs_image: "/var/lib/firecracker/rootfs/privileged-rootfs.ext4"
      resources:
        mem_size_mib: 4096
        vcpu_count: 4
      ttl_default: 1800
      ttl_max: 3600
```

## Environment Variables

The following environment variables can be used to configure the provisioner:

```bash
# General
PROVISIONER_TYPE=container          # or firecracker

# Container-specific
CONTAINER_RUNTIME=docker            # or podman
CONTAINER_IMAGE=sshbox-base:latest
ENABLE_RECORDING=true

# Firecracker-specific
KERNEL_IMAGE=/var/lib/firecracker/kernel/vmlinux
ROOTFS_IMAGE=/var/lib/firecracker/rootfs/rootfs.ext4
VM_MEM_SIZE=2048                   # in MB
VM_VCPU_COUNT=2
FIRECRACKER_SOCKET_DIR=/tmp
```