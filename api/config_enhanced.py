#!/usr/bin/env python3
"""
Comprehensive Configuration Module for sshBox

Provides unified configuration management with proper fallbacks,
validation, and production-ready settings.

Usage:
    from api.config_enhanced import get_config, Config
    
    config = get_config()
    gateway_secret = config.security.gateway_secret
    web_port = config.web.port
"""
import os
import sys
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import timedelta

logger = logging.getLogger("config_enhanced")


@dataclass
class SecurityConfig:
    """Security configuration"""
    gateway_secret: str = ""
    secret_min_length: int = 32
    token_max_age_seconds: int = 300
    allowed_profiles: List[str] = field(default_factory=lambda: ["dev", "debug", "secure-shell", "privileged", "interview"])
    rate_limit_enabled: bool = True
    rate_limit_request: str = "5/minute"
    rate_limit_sessions: str = "10/minute"
    rate_limit_destroy: str = "20/hour"
    ssh_min_rsa_bits: int = 2048
    allowed_key_types: List[str] = field(default_factory=lambda: ["ssh-rsa", "ssh-ed25519", "ecdsa-sha2-nistp256"])
    
    def __post_init__(self):
        if not self.gateway_secret:
            self.gateway_secret = os.environ.get('SSHBOX_SECURITY_GATEWAY_SECRET', '')
        if self.gateway_secret and len(self.gateway_secret) < self.secret_min_length:
            # SECURITY FIX: Reject weak secrets instead of just warning
            raise ValueError(f"Gateway secret must be at least {self.secret_min_length} characters (got {len(self.gateway_secret)})")
        
        # Additional secret strength validation
        if self.gateway_secret:
            has_upper = any(c.isupper() for c in self.gateway_secret)
            has_lower = any(c.islower() for c in self.gateway_secret)
            has_digit = any(c.isdigit() for c in self.gateway_secret)
            has_special = any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?' for c in self.gateway_secret)
            complexity_score = sum([has_upper, has_lower, has_digit, has_special])
            
            if complexity_score < 3:
                raise ValueError(
                    "Gateway secret must contain at least 3 of: uppercase, lowercase, digits, special characters"
                )


@dataclass
class DatabaseConfig:
    """Database configuration"""
    db_type: str = "sqlite"
    sqlite_path: str = "/var/lib/sshbox/sessions.db"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "sshbox"
    postgres_user: str = "sshbox_user"
    postgres_pass: str = ""
    pool_size: int = 10
    pool_timeout: int = 30
    
    def __post_init__(self):
        self.db_type = os.environ.get('SSHBOX_DB_DB_TYPE', self.db_type)
        if self.db_type == "sqlite":
            self.sqlite_path = os.environ.get('SSHBOX_DB_SQLITE_PATH', self.sqlite_path)
        else:
            self.postgres_host = os.environ.get('SSHBOX_DB_POSTGRES_HOST', self.postgres_host)
            self.postgres_port = int(os.environ.get('SSHBOX_DB_POSTGRES_PORT', self.postgres_port))
            self.postgres_db = os.environ.get('SSHBOX_DB_POSTGRES_DB', self.postgres_db)
            self.postgres_user = os.environ.get('SSHBOX_DB_POSTGRES_USER', self.postgres_user)
            self.postgres_pass = os.environ.get('SSHBOX_DB_POSTGRES_PASS', self.postgres_pass)


@dataclass
class WebConfig:
    """Web terminal configuration"""
    port: int = 3000
    gateway_url: str = "http://localhost:8080"
    cors_enabled: bool = True
    cors_origins: List[str] = field(default_factory=lambda: ["http://localhost:3000", "http://localhost:8080"])
    
    def __post_init__(self):
        self.port = int(os.environ.get('SSHBOX_WEB_PORT', self.port))
        self.gateway_url = os.environ.get('SSHBOX_WEB_GATEWAY_URL', self.gateway_url)
        cors_origins = os.environ.get('SSHBOX_WEB_CORS_ORIGINS', '')
        if cors_origins:
            self.cors_origins = cors_origins.split(',')


@dataclass
class InterviewConfig:
    """Interview mode configuration"""
    api_port: int = 8083
    storage_dir: str = "/var/lib/sshbox/interviews"
    gateway_url: str = "http://localhost:8080"
    default_ttl: int = 3600
    max_ttl: int = 7200
    default_problem: str = "two_sum"
    default_language: str = "python"
    recording_enabled: bool = True
    
    def __post_init__(self):
        self.api_port = int(os.environ.get('SSHBOX_INTERVIEW_API_PORT', self.api_port))
        self.storage_dir = os.environ.get('SSHBOX_INTERVIEW_STORAGE', self.storage_dir)
        self.gateway_url = os.environ.get('SSHBOX_INTERVIEW_GATEWAY_URL', self.gateway_url)
        self.default_ttl = int(os.environ.get('SSHBOX_INTERVIEW_DEFAULT_TTL', self.default_ttl))
        self.max_ttl = int(os.environ.get('SSHBOX_INTERVIEW_MAX_TTL', self.max_ttl))
        self.default_problem = os.environ.get('SSHBOX_INTERVIEW_DEFAULT_PROBLEM', self.default_problem)
        self.default_language = os.environ.get('SSHBOX_INTERVIEW_DEFAULT_LANGUAGE', self.default_language)


@dataclass
class PolicyConfig:
    """Policy engine (OPA) configuration"""
    opa_url: str = "http://localhost:8181"
    policy_package: str = "sshbox/authz"
    fallback_enabled: bool = True
    timeout_seconds: int = 5
    local_storage: str = "/etc/sshbox/policies"
    
    def __post_init__(self):
        self.opa_url = os.environ.get('SSHBOX_POLICY_OPA_URL', self.opa_url)
        self.policy_package = os.environ.get('SSHBOX_POLICY_PACKAGE', self.policy_package)
        self.fallback_enabled = os.environ.get('SSHBOX_POLICY_FALLBACK_ENABLED', 'true').lower() == 'true'
        self.local_storage = os.environ.get('SSHBOX_POLICY_LOCAL_STORAGE', self.local_storage)


@dataclass
class QuotaConfig:
    """Quota manager configuration"""
    db_path: str = "/var/lib/sshbox/quotas.db"
    redis_enabled: bool = True
    redis_cache_ttl: int = 60
    default_max_sessions: int = 10
    default_max_concurrent: int = 5
    default_max_daily: int = 50
    default_max_ttl: int = 7200
    
    def __post_init__(self):
        self.db_path = os.environ.get('SSHBOX_QUOTA_DB_PATH', self.db_path)
        self.redis_enabled = os.environ.get('SSHBOX_QUOTA_REDIS_ENABLED', 'true').lower() == 'true'


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration"""
    failure_threshold: int = 5
    success_threshold: int = 3
    recovery_timeout: int = 30
    
    def __post_init__(self):
        self.failure_threshold = int(os.environ.get('SSHBOX_CIRCUIT_BREAKER_FAILURE_THRESHOLD', self.failure_threshold))
        self.success_threshold = int(os.environ.get('SSHBOX_CIRCUIT_BREAKER_SUCCESS_THRESHOLD', self.success_threshold))
        self.recovery_timeout = int(os.environ.get('SSHBOX_CIRCUIT_BREAKER_RECOVERY_TIMEOUT', self.recovery_timeout))


@dataclass
class MonitoringConfig:
    """Monitoring configuration"""
    prometheus_enabled: bool = True
    prometheus_port: int = 9090
    grafana_enabled: bool = True
    grafana_port: int = 3001
    health_check_interval: int = 30
    metrics_endpoint: str = "/metrics"
    
    def __post_init__(self):
        self.prometheus_enabled = os.environ.get('SSHBOX_MONITORING_PROMETHEUS_ENABLED', 'true').lower() == 'true'
        self.grafana_enabled = os.environ.get('SSHBOX_MONITORING_GRAFANA_ENABLED', 'true').lower() == 'true'


@dataclass
class StorageConfig:
    """Storage configuration"""
    recordings_dir: str = "/var/lib/sshbox/recordings"
    recordings_retention_days: int = 7
    enable_recording: bool = True
    max_recording_size_mb: int = 100
    logs_dir: str = "/var/log/sshbox"
    log_level: str = "INFO"
    log_format: str = "json"
    metrics_file: str = "/var/lib/sshbox/metrics.json"
    
    def __post_init__(self):
        self.recordings_dir = os.environ.get('SSHBOX_STORAGE_RECORDINGS_DIR', self.recordings_dir)
        self.recordings_retention_days = int(os.environ.get('SSHBOX_STORAGE_RECORDINGS_RETENTION_DAYS', self.recordings_retention_days))
        self.enable_recording = os.environ.get('SSHBOX_STORAGE_ENABLE_RECORDING', 'true').lower() == 'true'
        self.logs_dir = os.environ.get('SSHBOX_STORAGE_LOGS_DIR', self.logs_dir)
        self.log_level = os.environ.get('SSHBOX_STORAGE_LOG_LEVEL', self.log_level)
        self.log_format = os.environ.get('SSHBOX_STORAGE_LOG_FORMAT', self.log_format)


@dataclass
class Config:
    """Main configuration class"""
    app_name: str = "sshBox"
    app_version: str = "2.0.0"
    debug: bool = False
    environment: str = "development"
    
    security: SecurityConfig = field(default_factory=SecurityConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    web: WebConfig = field(default_factory=WebConfig)
    interview: InterviewConfig = field(default_factory=InterviewConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    quota: QuotaConfig = field(default_factory=QuotaConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    
    def validate(self) -> List[str]:
        """Validate configuration and return list of errors"""
        errors = []
        
        # Security validation
        if not self.security.gateway_secret:
            errors.append("GATEWAY_SECRET is required")
        elif len(self.security.gateway_secret) < self.security.secret_min_length:
            errors.append(f"GATEWAY_SECRET must be at least {self.security.secret_min_length} characters")
        
        # Storage validation
        try:
            Path(self.storage.recordings_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            errors.append(f"Cannot create recordings directory: {e}")
        
        try:
            Path(self.storage.logs_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            errors.append(f"Cannot create logs directory: {e}")
        
        # Interview storage validation
        try:
            Path(self.interview.storage_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            errors.append(f"Cannot create interview storage directory: {e}")
        
        return errors
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary (safe for logging)"""
        return {
            "app_name": self.app_name,
            "app_version": self.app_version,
            "debug": self.debug,
            "environment": self.environment,
            "security": {
                "gateway_secret": "[REDACTED]" if self.security.gateway_secret else "",
                "secret_min_length": self.security.secret_min_length,
                "token_max_age_seconds": self.security.token_max_age_seconds,
                "allowed_profiles": self.security.allowed_profiles,
            },
            "database": {
                "db_type": self.database.db_type,
                "sqlite_path": self.database.sqlite_path,
            },
            "web": {
                "port": self.web.port,
                "gateway_url": self.web.gateway_url,
            },
            "interview": {
                "api_port": self.interview.api_port,
                "storage_dir": self.interview.storage_dir,
            },
            "policy": {
                "opa_url": self.policy.opa_url,
                "fallback_enabled": self.policy.fallback_enabled,
            },
            "monitoring": {
                "prometheus_enabled": self.monitoring.prometheus_enabled,
                "grafana_enabled": self.monitoring.grafana_enabled,
            },
        }


# Global configuration instance
_config: Optional[Config] = None


def get_config() -> Config:
    """Get or create global configuration instance"""
    global _config
    if _config is None:
        _config = Config()
        errors = _config.validate()
        if errors:
            logger.error(f"Configuration validation errors: {errors}")
            # Don't raise in development, just warn
            if os.environ.get('SSHBOX_ENVIRONMENT') == 'production':
                raise ValueError(f"Configuration validation failed: {errors}")
        logger.info("Configuration loaded successfully")
        logger.debug(f"Configuration: {_config.to_dict()}")
    return _config


def reload_config() -> Config:
    """Force reload configuration"""
    global _config
    _config = None
    return get_config()


# Convenience functions
def get_security_config() -> SecurityConfig:
    """Get security configuration"""
    return get_config().security


def get_database_config() -> DatabaseConfig:
    """Get database configuration"""
    return get_config().database


def get_web_config() -> WebConfig:
    """Get web configuration"""
    return get_config().web


def get_interview_config() -> InterviewConfig:
    """Get interview configuration"""
    return get_config().interview


def get_policy_config() -> PolicyConfig:
    """Get policy configuration"""
    return get_config().policy


def get_quota_config() -> QuotaConfig:
    """Get quota configuration"""
    return get_config().quota


def get_circuit_breaker_config() -> CircuitBreakerConfig:
    """Get circuit breaker configuration"""
    return get_config().circuit_breaker


def get_monitoring_config() -> MonitoringConfig:
    """Get monitoring configuration"""
    return get_config().monitoring


def get_storage_config() -> StorageConfig:
    """Get storage configuration"""
    return get_config().storage
