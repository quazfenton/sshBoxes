#!/usr/bin/env python3
"""
Centralized configuration management for sshBox
Using Pydantic for validation and type safety
"""
import os
import re
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import BaseSettings, Field, validator, SecretStr, ValidationError
from pydantic.env_settings import EnvSettingsSource
import logging

logger = logging.getLogger("config")


class DatabaseSettings(BaseSettings):
    """Database configuration"""
    db_type: str = Field(default="sqlite", description="Database type: sqlite or postgresql")
    sqlite_path: str = Field(default="/var/lib/sshbox/sessions.db")
    postgres_host: str = Field(default="localhost")
    postgres_port: int = Field(default=5432)
    postgres_db: str = Field(default="sshbox")
    postgres_user: str = Field(default="sshbox_user")
    postgres_pass: SecretStr = Field(default="")
    pool_size: int = Field(default=10, ge=1, le=100)
    pool_timeout: int = Field(default=30, ge=1)
    
    class Config:
        env_prefix = "SSHBOX_DB_"
    
    @validator('sqlite_path')
    def validate_sqlite_path(cls, v):
        """Ensure SQLite path is valid and parent directory exists"""
        path = Path(v)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not os.access(path.parent, os.W_OK):
                raise ValueError(f"Parent directory not writable: {path.parent}")
        except Exception as e:
            raise ValueError(f"Invalid SQLite path: {e}")
        return v


class SecuritySettings(BaseSettings):
    """Security configuration"""
    gateway_secret: SecretStr = Field(..., description="HMAC secret for token validation")
    secret_min_length: int = Field(default=32, ge=16)
    token_max_age_seconds: int = Field(default=300, ge=60)  # 5 minutes
    allowed_profiles: List[str] = Field(
        default=["dev", "debug", "secure-shell", "privileged"]
    )
    require_mfa: bool = Field(default=False)
    session_timeout_minutes: int = Field(default=60, ge=1)
    
    class Config:
        env_prefix = "SSHBOX_SECURITY_"
    
    @validator('gateway_secret')
    def validate_secret(cls, v):
        """Validate secret meets security requirements"""
        secret_value = v.get_secret_value()
        
        if not secret_value:
            raise ValueError("Gateway secret cannot be empty")
        
        if len(secret_value) < cls.__fields__['secret_min_length'].default:
            raise ValueError(
                f"Gateway secret must be at least {cls.__fields__['secret_min_length'].default} characters"
            )
        
        # Check for minimum entropy
        has_upper = any(c.isupper() for c in secret_value)
        has_lower = any(c.islower() for c in secret_value)
        has_digit = any(c.isdigit() for c in secret_value)
        has_special = any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?' for c in secret_value)
        
        entropy_score = sum([has_upper, has_lower, has_digit, has_special])
        if entropy_score < 3:
            raise ValueError(
                "Gateway secret must contain at least 3 of: uppercase, lowercase, digits, special characters"
            )
        
        return v
    
    @validator('allowed_profiles')
    def validate_profiles(cls, v):
        """Validate profile names"""
        if not v:
            raise ValueError("At least one profile must be allowed")
        
        valid_profile_pattern = re.compile(r'^[a-z][a-z0-9-]{0,31}$')
        for profile in v:
            if not valid_profile_pattern.match(profile):
                raise ValueError(
                    f"Invalid profile name: {profile}. Must start with letter, "
                    "contain only lowercase letters, numbers, and hyphens, max 32 chars"
                )
        
        return v


class ProvisionerSettings(BaseSettings):
    """Provisioner configuration"""
    provisioner_type: str = Field(default="container", description="container or firecracker")
    container_image: str = Field(default="sshbox-base:latest")
    container_runtime: str = Field(default="docker", description="docker or podman")
    enable_recording: bool = Field(default=True)
    
    # Firecracker settings
    firecracker_kernel: str = Field(default="/var/lib/firecracker/kernel/vmlinux")
    firecracker_rootfs: str = Field(default="/var/lib/firecracker/rootfs/rootfs.ext4")
    firecracker_socket_dir: str = Field(default="/var/run/firecracker")
    vm_mem_size_mib: int = Field(default=2048, ge=512)
    vm_vcpu_count: int = Field(default=2, ge=1, le=8)
    
    class Config:
        env_prefix = "SSHBOX_PROVISIONER_"
    
    @validator('provisioner_type')
    def validate_provisioner_type(cls, v):
        if v not in ("container", "firecracker"):
            raise ValueError("provisioner_type must be 'container' or 'firecracker'")
        return v
    
    @validator('container_runtime')
    def validate_container_runtime(cls, v):
        if v not in ("docker", "podman"):
            raise ValueError("container_runtime must be 'docker' or 'podman'")
        return v


class RecordingSettings(BaseSettings):
    """Session recording configuration"""
    enable_recording: bool = Field(default=True)
    recordings_dir: str = Field(default="/var/lib/sshbox/recordings")
    retention_days: int = Field(default=7, ge=1)
    recording_format: str = Field(default="asciicast", description="asciicast or typescript")
    max_recording_size_mb: int = Field(default=100, ge=10)
    
    class Config:
        env_prefix = "SSHBOX_RECORDING_"
    
    @validator('recordings_dir')
    def validate_recordings_dir(cls, v):
        """Ensure recordings directory is valid and writable"""
        path = Path(v)
        try:
            path.mkdir(parents=True, exist_ok=True)
            if not os.access(path, os.W_OK):
                raise ValueError(f"Directory not writable: {path}")
        except Exception as e:
            raise ValueError(f"Invalid recordings directory: {e}")
        return v


class RateLimitSettings(BaseSettings):
    """Rate limiting configuration"""
    request_limit: str = Field(default="5/minute")
    sessions_limit: str = Field(default="10/minute")
    destroy_limit: str = Field(default="20/hour")
    trusted_ips: Optional[str] = Field(default=None)
    enabled: bool = Field(default=True)
    
    class Config:
        env_prefix = "SSHBOX_RATELIMIT_"
    
    @validator('request_limit', 'sessions_limit', 'destroy_limit')
    def validate_rate_limit(cls, v):
        """Validate rate limit format"""
        pattern = r'^\d+/(second|minute|hour|day)$'
        if not re.match(pattern, v):
            raise ValueError(
                f"Invalid rate limit format: {v}. Must be like '5/minute'"
            )
        return v


class NetworkSettings(BaseSettings):
    """Network configuration"""
    gateway_host: str = Field(default="0.0.0.0")
    gateway_port: int = Field(default=8080, ge=1, le=65535)
    allowed_origins: List[str] = Field(default=[])
    cors_enabled: bool = Field(default=True)
    ssl_enabled: bool = Field(default=False)
    ssl_cert_path: Optional[str] = Field(default=None)
    ssl_key_path: Optional[str] = Field(default=None)
    
    class Config:
        env_prefix = "SSHBOX_NETWORK_"


class Settings(BaseSettings):
    """Main settings class for sshBox"""
    
    # Application
    app_name: str = Field(default="sshBox Gateway")
    debug: bool = Field(default=False)
    logs_dir: str = Field(default="/var/log/sshbox")
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json", description="json or console")
    
    # Sub-settings
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    provisioner: ProvisionerSettings = Field(default_factory=ProvisionerSettings)
    recording: RecordingSettings = Field(default_factory=RecordingSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    network: NetworkSettings = Field(default_factory=NetworkSettings)
    
    class Config:
        env_file = ".env"
        env_nested_delimiter = "__"
    
    def validate_all(self) -> List[str]:
        """
        Validate all settings and return list of errors
        
        Returns:
            List of error messages, empty if all valid
        """
        errors = []
        
        # Check logs directory
        try:
            logs_path = Path(self.logs_dir)
            logs_path.mkdir(parents=True, exist_ok=True)
            if not os.access(logs_path, os.W_OK):
                errors.append(f"Logs directory not writable: {self.logs_dir}")
        except Exception as e:
            errors.append(f"Cannot create logs directory: {e}")
        
        # Check recordings directory
        if self.recording.enable_recording:
            try:
                recordings_path = Path(self.recording.recordings_dir)
                recordings_path.mkdir(parents=True, exist_ok=True)
                if not os.access(recordings_path, os.W_OK):
                    errors.append(f"Recordings directory not writable: {self.recording.recordings_dir}")
            except Exception as e:
                errors.append(f"Cannot create recordings directory: {e}")
        
        # Check Firecracker paths if using Firecracker
        if self.provisioner.provisioner_type == "firecracker":
            if not os.path.exists(self.provisioner.firecracker_kernel):
                errors.append(f"Firecracker kernel not found: {self.provisioner.firecracker_kernel}")
            if not os.path.exists(self.provisioner.firecracker_rootfs):
                errors.append(f"Firecracker rootfs not found: {self.provisioner.firecracker_rootfs}")
            if not os.path.exists(self.provisioner.firecracker_socket_dir):
                try:
                    Path(self.provisioner.firecracker_socket_dir).mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    errors.append(f"Cannot create Firecracker socket directory: {e}")
        
        # Check SSL paths if SSL enabled
        if self.network.ssl_enabled:
            if self.network.ssl_cert_path and not os.path.exists(self.network.ssl_cert_path):
                errors.append(f"SSL certificate not found: {self.network.ssl_cert_path}")
            if self.network.ssl_key_path and not os.path.exists(self.network.ssl_key_path):
                errors.append(f"SSL key not found: {self.network.ssl_key_path}")
        
        return errors
    
    def to_log_dict(self) -> Dict[str, Any]:
        """Convert settings to dictionary safe for logging (excludes secrets)"""
        return {
            "app_name": self.app_name,
            "debug": self.debug,
            "logs_dir": self.logs_dir,
            "log_level": self.log_level,
            "database": {
                "db_type": self.database.db_type,
                "pool_size": self.database.pool_size,
            },
            "security": {
                "secret_min_length": self.security.secret_min_length,
                "token_max_age_seconds": self.security.token_max_age_seconds,
                "allowed_profiles": self.security.allowed_profiles,
            },
            "provisioner": {
                "provisioner_type": self.provisioner.provisioner_type,
                "container_image": self.provisioner.container_image,
            },
            "recording": {
                "enable_recording": self.recording.enable_recording,
                "retention_days": self.recording.retention_days,
            },
            "rate_limit": {
                "enabled": self.rate_limit.enabled,
                "request_limit": self.rate_limit.request_limit,
            },
            "network": {
                "gateway_port": self.network.gateway_port,
                "cors_enabled": self.network.cors_enabled,
                "ssl_enabled": self.network.ssl_enabled,
            }
        }


# Global settings instance
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get or create settings instance with validation"""
    global _settings
    if _settings is None:
        try:
            _settings = Settings()
            errors = _settings.validate_all()
            if errors:
                raise ConfigurationError(
                    "Configuration validation failed:\n" + "\n".join(errors)
                )
            logger.info("Configuration loaded and validated successfully")
            logger.debug(f"Configuration: {_settings.to_log_dict()}")
        except ValidationError as e:
            raise ConfigurationError(f"Configuration validation error: {e}")
    return _settings


def reload_settings() -> Settings:
    """Force reload settings (useful for testing)"""
    global _settings
    _settings = None
    return get_settings()


class ConfigurationError(Exception):
    """Raised when configuration validation fails"""
    
    def __init__(self, message: str, errors: List[str] = None):
        self.message = message
        self.errors = errors or []
        super().__init__(self.message)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": "CONFIGURATION_ERROR",
            "message": self.message,
            "validation_errors": self.errors
        }


# Convenience functions
def get_secret_value(secret: SecretStr) -> str:
    """Safely get secret value"""
    return secret.get_secret_value() if secret else ""


def is_debug_mode() -> bool:
    """Check if debug mode is enabled"""
    return get_settings().debug


def get_database_url() -> str:
    """Get database connection URL"""
    settings = get_settings()
    if settings.database.db_type == "postgresql":
        return (
            f"postgresql://{settings.database.postgres_user}:"
            f"{get_secret_value(settings.database.postgres_pass)}@"
            f"{settings.database.postgres_host}:{settings.database.postgres_port}/"
            f"{settings.database.postgres_db}"
        )
    else:
        return f"sqlite:///{settings.database.sqlite_path}"
