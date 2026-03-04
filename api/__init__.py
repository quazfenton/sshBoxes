"""
sshBox API Package

Provides core API functionality for sshBox ephemeral SSH environments.
"""

from api.exceptions import (
    SSHBoxError,
    TokenValidationError,
    TokenExpiredError,
    ProvisioningError,
    SessionNotFoundError,
    SessionAlreadyDestroyedError,
    ConfigurationError,
    DatabaseError,
    InvalidInputError,
    SSHKeyError,
    CircuitBreakerOpenError,
    QuotaExceededError,
    PolicyDeniedError,
    PathTraversalError,
    CommandInjectionError,
    ResourceExhaustedError,
)

from api.config import get_settings, Settings, ConfigurationError
from api.config_enhanced import (
    get_config,
    reload_config,
    get_security_config,
    get_database_config,
    get_web_config,
    get_interview_config,
    get_policy_config,
    get_quota_config,
    get_circuit_breaker_config,
    get_monitoring_config,
    get_storage_config,
    Config,
    SecurityConfig,
    DatabaseConfig,
    WebConfig,
    InterviewConfig,
    PolicyConfig,
    QuotaConfig,
    CircuitBreakerConfig,
    MonitoringConfig,
    StorageConfig,
)

__version__ = "2.0.0"
__all__ = [
    # Exceptions
    "SSHBoxError",
    "TokenValidationError",
    "TokenExpiredError",
    "ProvisioningError",
    "SessionNotFoundError",
    "SessionAlreadyDestroyedError",
    "ConfigurationError",
    "DatabaseError",
    "InvalidInputError",
    "SSHKeyError",
    "CircuitBreakerOpenError",
    "QuotaExceededError",
    "PolicyDeniedError",
    "PathTraversalError",
    "CommandInjectionError",
    "ResourceExhaustedError",
    # Config (original)
    "get_settings",
    "Settings",
    "ConfigurationError",
    # Config (enhanced)
    "get_config",
    "reload_config",
    "get_security_config",
    "get_database_config",
    "get_web_config",
    "get_interview_config",
    "get_policy_config",
    "get_quota_config",
    "get_circuit_breaker_config",
    "get_monitoring_config",
    "get_storage_config",
    "Config",
    "SecurityConfig",
    "DatabaseConfig",
    "WebConfig",
    "InterviewConfig",
    "PolicyConfig",
    "QuotaConfig",
    "CircuitBreakerConfig",
    "MonitoringConfig",
    "StorageConfig",
]
