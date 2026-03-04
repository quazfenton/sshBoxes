#!/usr/bin/env python3
"""
Enhanced Provisioner Service for sshBox
Manages the lifecycle of ephemeral containers and VMs with comprehensive error handling
"""
import os
import sys
import json
import subprocess
import time
import threading
import re
import logging
import socket
import struct
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from dataclasses import dataclass
from enum import Enum

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
import uvicorn

# Local imports
from api.config import get_settings, Settings
from api.security import InputValidator, SSHKeyValidator
from api.metrics import get_metrics_collector

# Configure logging
settings = get_settings()
logs_dir = settings.storage.logs_dir
os.makedirs(logs_dir, exist_ok=True)

logger = logging.getLogger("provisioner")
logger.setLevel(getattr(logging, settings.storage.log_level))

formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# File handler
file_handler = logging.handlers.RotatingFileHandler(
    os.path.join(logs_dir, "provisioner.log"),
    maxBytes=settings.storage.log_max_bytes,
    backupCount=settings.storage.log_backup_count
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)


class ProvisionerType(str, Enum):
    CONTAINER = "container"
    FIRECRACKER = "firecracker"


class FirecrackerIPDiscovery:
    """
    Discovers IP addresses for Firecracker VMs using multiple methods.
    
    Methods (in order of preference):
    1. Read from DHCP lease file (dnsmasq.leases)
    2. Scan ARP table for VM MAC address
    3. Use configured static IP from network config
    4. Fallback to default IP (with warning)
    """
    
    DEFAULT_IP = "172.16.0.10"
    DEFAULT_MAC_PREFIX = "AA:FC:00"  # Firecracker default MAC prefix
    DHCP_LEASE_FILES = [
        "/var/lib/misc/dnsmasq.leases",
        "/var/lib/dnsmasq/dnsmasq.leases",
        "/tmp/dnsmasq.leases",
    ]
    
    @classmethod
    def discover_ip(cls, session_id: str, vm_mac: str = None, logger=None) -> str:
        """
        Discover the IP address of a Firecracker VM
        
        Args:
            session_id: Session identifier (used for logging)
            vm_mac: VM's MAC address (optional, uses default prefix if not provided)
            logger: Logger instance for logging
        
        Returns:
            Discovered IP address or default fallback
        """
        if logger is None:
            logger = logging.getLogger("provisioner")
        
        if vm_mac is None:
            vm_mac = f"{cls.DEFAULT_MAC_PREFIX}:00:00:01"
        
        # Method 1: Try DHCP lease file
        ip = cls._read_dhcp_lease(vm_mac, logger)
        if ip:
            logger.info(f"Discovered VM IP from DHCP lease: {ip}")
            return ip
        
        # Method 2: Try ARP table scan
        ip = cls._scan_arp_table(vm_mac, logger)
        if ip:
            logger.info(f"Discovered VM IP from ARP table: {ip}")
            return ip
        
        # Method 3: Try to ping sweep the network
        ip = cls._ping_sweep(vm_mac, logger)
        if ip:
            logger.info(f"Discovered VM IP from ping sweep: {ip}")
            return ip
        
        # Fallback: Use default IP with warning
        logger.warning(
            f"Could not discover VM IP for session {session_id}, "
            f"using default {cls.DEFAULT_IP}. Configure DHCP lease monitoring for better accuracy."
        )
        return cls.DEFAULT_IP
    
    @classmethod
    def _read_dhcp_lease(cls, vm_mac: str, logger: logging.Logger) -> Optional[str]:
        """Read IP from DHCP lease file"""
        vm_mac = vm_mac.upper().replace(":", "-")
        
        for lease_file in cls.DHCP_LEASE_FILES:
            try:
                if os.path.exists(lease_file):
                    with open(lease_file, 'r') as f:
                        for line in f:
                            parts = line.strip().split()
                            if len(parts) >= 4:
                                # dnsmasq.leases format: expiry MAC IP hostname
                                lease_mac = parts[1].upper()
                                lease_ip = parts[2]
                                if lease_mac == vm_mac or lease_mac.startswith(cls.DEFAULT_MAC_PREFIX.replace(":", "").upper()):
                                    logger.debug(f"Found DHCP lease: {lease_mac} -> {lease_ip}")
                                    return lease_ip
            except (IOError, PermissionError) as e:
                logger.debug(f"Could not read lease file {lease_file}: {e}")
                continue
        
        return None
    
    @classmethod
    def _scan_arp_table(cls, vm_mac: str, logger: logging.Logger) -> Optional[str]:
        """Scan ARP table for VM MAC address"""
        try:
            # Use arp command to list ARP table
            result = subprocess.run(
                ["arp", "-an"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                # Parse ARP output (format varies by OS)
                # Linux: ? (172.16.0.10) at aa:fc:00:00:00:01 [ether] on eth0
                # BSD/macOS: ? (172.16.0.10) at aa:fc:00:00:00:01 on en0
                vm_mac_lower = vm_mac.lower()
                for line in result.stdout.split('\n'):
                    if vm_mac_lower in line.lower():
                        # Extract IP from line
                        match = re.search(r'\((\d+\.\d+\.\d+\.\d+)\)', line)
                        if match:
                            ip = match.group(1)
                            logger.debug(f"Found ARP entry: {vm_mac} -> {ip}")
                            return ip
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.debug(f"ARP scan failed: {e}")
        
        return None
    
    @classmethod
    def _ping_sweep(cls, vm_mac: str, logger: logging.Logger) -> Optional[str]:
        """
        Ping sweep the local network to find VM
        This is a last resort as it's slow and may be blocked by firewalls
        """
        # Common Firecracker network ranges
        network_ranges = [
            "172.16.0.{0}/24",
            "10.0.0.{0}/24",
            "192.168.0.{0}/24",
        ]
        
        for network_template in network_ranges:
            # Get local network prefix
            try:
                hostname = socket.gethostname()
                local_ip = socket.gethostbyname(hostname)
                network_prefix = '.'.join(local_ip.split('.')[:3])
                network = network_template.format(network_prefix)
                
                # Try a few IPs in the range (not full sweep to save time)
                base_ip = network.split('/')[0].rsplit('.', 1)[0]
                for i in range(2, 20):  # Try IPs .2 through .19
                    test_ip = f"{base_ip}.{i}"
                    if cls._try_ping(test_ip, logger):
                        logger.debug(f"Responsive IP found: {test_ip}")
                        return test_ip
            except Exception:
                continue
        
        return None
    
    @classmethod
    def _try_ping(cls, ip: str, logger: logging.Logger) -> bool:
        """Try to ping a single IP"""
        try:
            # Use system ping with timeout
            param = '-n' if os.name == 'nt' else '-c'
            timeout = '1'  # 1 second timeout
            
            result = subprocess.run(
                ['ping', param, '1', '-W', timeout, ip],
                capture_output=True,
                timeout=3
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
    
    @classmethod
    def allocate_static_ip(cls, session_id: str, network: str = "172.16.0.0/24") -> str:
        """
        Allocate a static IP from a network range
        Uses a simple file-based allocation system
        
        Args:
            session_id: Session identifier
            network: Network range in CIDR notation
        
        Returns:
            Allocated IP address
        """
        # Parse network
        network_base = network.split('/')[0].rsplit('.', 1)[0]
        
        # Use file-based allocation tracking
        allocation_file = Path("/tmp/sshbox_ip_allocations.json")
        allocations = {}
        
        if allocation_file.exists():
            try:
                with open(allocation_file, 'r') as f:
                    allocations = json.load(f)
            except (json.JSONDecodeError, IOError):
                allocations = {}
        
        # Find available IP
        for i in range(2, 254):
            ip = f"{network_base}.{i}"
            if ip not in allocations.values():
                # Allocate this IP
                allocations[session_id] = ip
                
                # Save allocations
                try:
                    with open(allocation_file, 'w') as f:
                        json.dump(allocations, f, indent=2)
                except IOError as e:
                    logging.warning(f"Could not save IP allocation: {e}")
                
                return ip
        
        # No available IPs
        raise RuntimeError(f"No available IPs in network {network}")
    
    @classmethod
    def release_ip(cls, session_id: str) -> bool:
        """Release an allocated IP"""
        allocation_file = Path("/tmp/sshbox_ip_allocations.json")
        
        if not allocation_file.exists():
            return False
        
        try:
            with open(allocation_file, 'r') as f:
                allocations = json.load(f)
            
            if session_id in allocations:
                del allocations[session_id]
                
                with open(allocation_file, 'w') as f:
                    json.dump(allocations, f, indent=2)
                
                return True
        except (json.JSONDecodeError, IOError) as e:
            logging.warning(f"Could not release IP allocation: {e}")
        
        return False


@dataclass
class ProvisionResult:
    """Result of a provisioning operation"""
    success: bool
    session_id: str
    container_name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    user: str = "boxuser"
    profile: Optional[str] = None
    error: Optional[str] = None
    provision_time: Optional[float] = None
    
    def to_connection_info(self) -> Dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "session_id": self.session_id,
            "profile": self.profile
        }


class ContainerProvisioner:
    """Container-based provisioner using Docker"""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ssh_validator = SSHKeyValidator()
    
    def provision(
        self,
        session_id: str,
        pubkey: str,
        profile: str,
        ttl: int
    ) -> ProvisionResult:
        """Provision a container for the session"""
        start_time = time.time()
        logger.info(f"Starting container provisioning for session {session_id}")
        
        try:
            # Validate inputs
            is_valid, error = InputValidator.validate_session_id(session_id)
            if not is_valid:
                raise ValueError(f"Invalid session_id: {error}")
            
            is_valid, error = self.ssh_validator.validate(pubkey)
            if not is_valid:
                raise ValueError(f"Invalid pubkey: {error}")
            
            # Generate container name
            container_name = f"sshbox_{session_id}"
            
            # Check if container already exists
            result = subprocess.run(
                ["docker", "ps", "-a", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
                capture_output=True,
                text=True
            )
            if container_name in result.stdout:
                logger.warning(f"Container {container_name} already exists, removing...")
                self._cleanup_container(container_name)
            
            # Create container
            logger.info(f"Creating container {container_name} with image {self.settings.provisioner.container_image}")
            
            result = subprocess.run(
                [
                    "docker", "run", "-d",
                    "--name", container_name,
                    "-P",  # Publish all exposed ports
                    "--rm",  # Auto-remove on stop
                    "--label", f"sshbox.session_id={session_id}",
                    "--label", f"sshbox.profile={profile}",
                    "--label", f"sshbox.ttl={ttl}",
                    self.settings.provisioner.container_image,
                    "sleep", "infinity"
                ],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                raise RuntimeError(f"Failed to create container: {result.stderr}")
            
            container_id = result.stdout.strip()
            logger.info(f"Container created: {container_id}")
            
            # Wait for container to be ready
            time.sleep(1)
            
            # Get SSH port
            result = subprocess.run(
                ["docker", "port", container_name, "22"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                raise RuntimeError(f"Failed to get SSH port: {result.stderr}")
            
            # Parse port from output (format: 0.0.0.0:PORT)
            port_match = re.search(r':(\d+)', result.stdout)
            if not port_match:
                raise RuntimeError("Failed to parse SSH port from docker port output")
            
            ssh_port = int(port_match.group(1))
            logger.info(f"SSH port mapped: {ssh_port}")
            
            # Inject SSH key
            logger.info(f"Injecting SSH key for session {session_id}")
            
            # Create SSH directory in container
            result = subprocess.run(
                ["docker", "exec", container_name, "mkdir", "-p", "/home/boxuser/.ssh"],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                raise RuntimeError(f"Failed to create SSH directory: {result.stderr}")
            
            # Write authorized_keys
            process = subprocess.Popen(
                ["docker", "exec", "-i", container_name, "tee", "/home/boxuser/.ssh/authorized_keys"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = process.communicate(input=pubkey.encode())
            
            if process.returncode != 0:
                raise RuntimeError(f"Failed to inject SSH key: {stderr.decode()}")
            
            # Set permissions
            subprocess.run(
                ["docker", "exec", container_name, "chown", "-R", "boxuser:boxuser", "/home/boxuser/.ssh"],
                capture_output=True,
                timeout=10
            )
            subprocess.run(
                ["docker", "exec", container_name, "chmod", "700", "/home/boxuser/.ssh"],
                capture_output=True,
                timeout=10
            )
            subprocess.run(
                ["docker", "exec", container_name, "chmod", "600", "/home/boxuser/.ssh/authorized_keys"],
                capture_output=True,
                timeout=10
            )
            
            # Get host IP
            host_ip = self._get_host_ip()
            
            provision_time = time.time() - start_time
            logger.info(f"Container provisioned successfully in {provision_time:.2f}s")
            
            return ProvisionResult(
                success=True,
                session_id=session_id,
                container_name=container_name,
                host=host_ip,
                port=ssh_port,
                profile=profile,
                provision_time=provision_time
            )
            
        except subprocess.TimeoutExpired as e:
            logger.error(f"Provisioning timed out: {e}")
            self._cleanup_container(container_name if 'container_name' in locals() else f"sshbox_{session_id}")
            return ProvisionResult(
                success=False,
                session_id=session_id,
                error=f"Provisioning timed out: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Provisioning failed: {e}", exc_info=True)
            self._cleanup_container(container_name if 'container_name' in locals() else f"sshbox_{session_id}")
            return ProvisionResult(
                success=False,
                session_id=session_id,
                error=str(e)
            )
    
    def _cleanup_container(self, container_name: str):
        """Clean up a container"""
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                timeout=10
            )
            logger.info(f"Cleaned up container: {container_name}")
        except Exception as e:
            logger.warning(f"Failed to cleanup container {container_name}: {e}")
    
    def _get_host_ip(self) -> str:
        """Get host IP address"""
        try:
            # Try to get IP from docker0 interface
            result = subprocess.run(
                ["ip", "-4", "addr", "show", "docker0"],
                capture_output=True,
                text=True
            )
            match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', result.stdout)
            if match:
                return match.group(1)
        except Exception:
            pass
        
        # Fallback to localhost
        return "127.0.0.1"
    
    def destroy(self, container_name: str) -> Tuple[bool, str]:
        """Destroy a container"""
        try:
            logger.info(f"Destroying container: {container_name}")
            
            result = subprocess.run(
                ["docker", "rm", "-f", container_name],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                return False, result.stderr
            
            return True, "Container destroyed successfully"
            
        except subprocess.TimeoutExpired:
            return False, "Destroy timed out"
        except Exception as e:
            return False, str(e)


class FirecrackerProvisioner:
    """Firecracker VM-based provisioner"""
    
    def __init__(self, settings: Settings):
        self.settings = settings
        self.ssh_validator = SSHKeyValidator()
    
    def provision(
        self,
        session_id: str,
        pubkey: str,
        profile: str,
        ttl: int
    ) -> ProvisionResult:
        """Provision a Firecracker VM for the session"""
        start_time = time.time()
        logger.info(f"Starting Firecracker provisioning for session {session_id}")
        
        try:
            # Validate inputs
            is_valid, error = InputValidator.validate_session_id(session_id)
            if not is_valid:
                raise ValueError(f"Invalid session_id: {error}")
            
            is_valid, error = self.ssh_validator.validate(pubkey)
            if not is_valid:
                raise ValueError(f"Invalid pubkey: {error}")
            
            # Check if firecracker is available
            result = subprocess.run(
                ["firecracker", "--version"],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                raise RuntimeError("Firecracker is not installed or not in PATH")
            
            # Create session directory
            session_dir = Path(f"/tmp/firecracker-session-{session_id}")
            session_dir.mkdir(parents=True, exist_ok=True)
            
            # Copy rootfs
            rootfs_src = Path(self.settings.provisioner.rootfs_image)
            rootfs_dst = session_dir / "rootfs.ext4"
            
            if not rootfs_src.exists():
                raise RuntimeError(f"Rootfs image not found: {rootfs_src}")
            
            logger.info(f"Copying rootfs to {rootfs_dst}")
            
            # Use dd for efficient copy
            subprocess.run(
                ["dd", f"if={rootfs_src}", f"of={rootfs_dst}", "bs=4M", "status=none"],
                check=True
            )
            
            # Mount and inject SSH key
            mount_point = session_dir / "mnt"
            mount_point.mkdir(exist_ok=True)
            
            logger.info("Mounting rootfs to inject SSH key")
            
            try:
                subprocess.run(
                    ["sudo", "mount", "-o", "loop", str(rootfs_dst), str(mount_point)],
                    check=True,
                    capture_output=True
                )
                
                # Create SSH directory
                ssh_dir = mount_point / "home" / "boxuser" / ".ssh"
                ssh_dir.mkdir(parents=True, exist_ok=True)
                
                # Write authorized_keys
                (ssh_dir / "authorized_keys").write_text(pubkey)
                
                # Set permissions (using numeric IDs to avoid chroot issues)
                subprocess.run(
                    ["sudo", "chown", "-R", "1000:1000", str(ssh_dir)],
                    check=True,
                    capture_output=True
                )
                subprocess.run(
                    ["sudo", "chmod", "700", str(ssh_dir)],
                    check=True,
                    capture_output=True
                )
                subprocess.run(
                    ["sudo", "chmod", "600", str(ssh_dir / "authorized_keys")],
                    check=True,
                    capture_output=True
                )
                
            finally:
                # Unmount
                subprocess.run(
                    ["sudo", "umount", str(mount_point)],
                    capture_output=True
                )
            
            # Create Firecracker config
            socket_path = session_dir / "firecracker.sock"
            config = {
                "boot-source": {
                    "kernel_image_path": self.settings.provisioner.kernel_image,
                    "boot_args": "console=ttyS0 noapic reboot=k panic=1 pci=off nomodules ip=dhcp"
                },
                "drives": [
                    {
                        "drive_id": "rootfs",
                        "path_on_host": str(rootfs_dst),
                        "is_root_device": True,
                        "is_read_only": False
                    }
                ],
                "machine-config": {
                    "vcpu_count": self.settings.provisioner.vm_vcpu_count,
                    "mem_size_mib": self.settings.provisioner.vm_mem_size,
                    "ht_enabled": False
                },
                "network-interfaces": [
                    {
                        "iface_id": "eth0",
                        "guest_mac": "AA:FC:00:00:00:01",
                        "host_dev_name": "tap0"
                    }
                ]
            }
            
            config_path = session_dir / "config.json"
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
            
            # Start Firecracker
            logger.info("Starting Firecracker VM")
            
            log_fifo = session_dir / "log.fifo"
            log_fifo.touch()

            cmd = [
                "firecracker",
                "--api-sock", str(socket_path),
                "--config-file", str(config_path)
            ]

            # Start in background
            with open(log_fifo, 'w') as log_file:
                process = subprocess.Popen(
                    cmd,
                    stdout=log_file,
                    stderr=log_file,
                    start_new_session=True
                )

            # Wait for VM to start
            time.sleep(3)

            # Get VM IP using discovery mechanism
            # Extract MAC from config (or use default)
            vm_mac = network_interface.get("guest_mac", f"{FirecrackerIPDiscovery.DEFAULT_MAC_PREFIX}:00:00:01")
            
            # Try to discover IP, with static IP allocation as fallback
            try:
                # First try to allocate a static IP for this session
                vm_ip = FirecrackerIPDiscovery.allocate_static_ip(session_id)
                logger.info(f"Allocated static IP {vm_ip} for session {session_id}")
            except RuntimeError as e:
                # If allocation fails, try discovery
                logger.warning(f"Static IP allocation failed: {e}, attempting discovery...")
                vm_ip = FirecrackerIPDiscovery.discover_ip(session_id, vm_mac, logger)
            
            ssh_port = 22

            provision_time = time.time() - start_time
            logger.info(f"Firecracker VM provisioned successfully in {provision_time:.2f}s (IP: {vm_ip})")

            return ProvisionResult(
                success=True,
                session_id=session_id,
                container_name=str(session_dir),
                host=vm_ip,
                port=ssh_port,
                profile=profile,
                provision_time=provision_time
            )
            
        except Exception as e:
            logger.error(f"Firecracker provisioning failed: {e}", exc_info=True)
            self._cleanup(session_id)
            return ProvisionResult(
                success=False,
                session_id=session_id,
                error=str(e)
            )
    
    def destroy(self, session_id: str) -> Tuple[bool, str]:
        """Destroy a Firecracker VM"""
        try:
            logger.info(f"Destroying Firecracker VM for session {session_id}")

            session_dir = Path(f"/tmp/firecracker-session-{session_id}")
            socket_path = session_dir / "firecracker.sock"

            # Graceful shutdown
            if socket_path.exists():
                try:
                    subprocess.run(
                        ["curl", "--unix-socket", str(socket_path),
                         "-X", "PUT", "http://localhost/actions",
                         "-H", "Content-Type: application/json",
                         "-d", '{"action_type": "SendCtrlAltDel"}'],
                        timeout=5
                    )
                    time.sleep(3)
                except Exception:
                    pass

            # Kill process
            result = subprocess.run(
                ["pkill", "-f", f"firecracker.*{session_id}"],
                capture_output=True
            )

            # Clean up directory
            if session_dir.exists():
                subprocess.run(["sudo", "rm", "-rf", str(session_dir)])

            # Release allocated IP
            try:
                FirecrackerIPDiscovery.release_ip(session_id)
                logger.debug(f"Released IP allocation for session {session_id}")
            except Exception as e:
                logger.warning(f"Could not release IP for session {session_id}: {e}")

            return True, "Firecracker VM destroyed successfully"

        except Exception as e:
            return False, str(e)
    
    def _cleanup(self, session_id: str):
        """Clean up session resources"""
        self.destroy(session_id)


# FastAPI application
app = FastAPI(title="sshBox Provisioner API")

# Initialize provisioners
settings = get_settings()
container_provisioner = ContainerProvisioner(settings)
firecracker_provisioner = FirecrackerProvisioner(settings)
metrics = get_metrics_collector()


class ProvisionRequest(BaseModel):
    session_id: str
    pubkey: str
    profile: str = "dev"
    ttl: int = 1800
    
    @validator('session_id')
    def validate_session_id(cls, v):
        is_valid, error = InputValidator.validate_session_id(v)
        if not is_valid:
            raise ValueError(error)
        return v
    
    @validator('pubkey')
    def validate_pubkey(cls, v):
        ssh_validator = SSHKeyValidator()
        is_valid, error = ssh_validator.validate(v)
        if not is_valid:
            raise ValueError(error)
        return v
    
    @validator('ttl')
    def validate_ttl(cls, v):
        if v < settings.provisioner.min_ttl:
            raise ValueError(f"TTL must be at least {settings.provisioner.min_ttl} seconds")
        if v > settings.provisioner.max_ttl:
            raise ValueError(f"TTL must be at most {settings.provisioner.max_ttl} seconds")
        return v


@app.post("/provision")
async def provision(request: ProvisionRequest):
    """Provision a new container or VM"""
    start_time = time.time()
    
    try:
        # Select provisioner
        if settings.provisioner.provisioner_type == "firecracker":
            provisioner = firecracker_provisioner
        else:
            provisioner = container_provisioner
        
        # Provision
        result = provisioner.provision(
            session_id=request.session_id,
            pubkey=request.pubkey,
            profile=request.profile,
            ttl=request.ttl
        )
        
        if not result.success:
            metrics.record_error("provision_failed")
            raise HTTPException(status_code=500, detail=result.error)
        
        # Record metrics
        metrics.record_session_creation(request.profile)
        metrics.record_timing("provision_time", result.provision_time or 0)
        
        return result.to_connection_info()
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Provisioning error: {e}", exc_info=True)
        metrics.record_error("provision_error")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/destroy/{session_id}")
async def destroy(session_id: str):
    """Destroy a container or VM"""
    try:
        # Get container name from database or state
        # For now, construct from session_id
        container_name = f"sshbox_{session_id}"
        
        if settings.provisioner.provisioner_type == "firecracker":
            success, message = firecracker_provisioner.destroy(session_id)
        else:
            success, message = container_provisioner.destroy(container_name)
        
        if not success:
            raise HTTPException(status_code=500, detail=message)
        
        metrics.record_session_destruction()
        
        return {"message": message, "session_id": session_id}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Destroy error: {e}", exc_info=True)
        metrics.record_error("destroy_error")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check():
    """Health check"""
    provisioner_type = settings.provisioner.provisioner_type
    
    # Check provisioner availability
    status = "healthy"
    details = {}
    
    if provisioner_type == "firecracker":
        result = subprocess.run(["firecracker", "--version"], capture_output=True, text=True)
        details["firecracker"] = "available" if result.returncode == 0 else "unavailable"
        if result.returncode != 0:
            status = "unhealthy"
    else:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True)
        details["docker"] = "available" if result.returncode == 0 else "unavailable"
        if result.returncode != 0:
            status = "unhealthy"
    
    return {
        "status": status,
        "provisioner_type": provisioner_type,
        "details": details,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/metrics")
async def get_metrics():
    """Get Prometheus metrics"""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(metrics.get_prometheus_metrics(), media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get('PROVISIONER_PORT', 8081)),
        log_level=settings.storage.log_level.lower()
    )
