#!/usr/bin/env python3
"""
Custom exceptions for sshBox
Provides a hierarchical exception structure for better error handling
"""


class SSHBoxError(Exception):
    """Base exception for all sshBox errors"""
    
    def __init__(self, message: str, code: str = None, details: dict = None):
        self.message = message
        self.code = code or "SSHBOX_ERROR"
        self.details = details or {}
        super().__init__(self.message)
    
    def to_dict(self) -> dict:
        """Convert exception to dictionary for API responses"""
        return {
            "error": self.code,
            "message": self.message,
            "details": self.details
        }


class TokenValidationError(SSHBoxError):
    """Raised when token validation fails"""
    
    def __init__(self, reason: str = "Invalid token", token_expired: bool = False):
        super().__init__(
            message=reason,
            code="TOKEN_VALIDATION_ERROR",
            details={"token_expired": token_expired}
        )


class TokenExpiredError(TokenValidationError):
    """Raised when token has expired"""
    
    def __init__(self, expired_since: str = None):
        super().__init__(
            reason="Token has expired",
            token_expired=True
        )
        self.details["expired_since"] = expired_since


class ProvisioningError(SSHBoxError):
    """Raised when container/VM provisioning fails"""
    
    def __init__(
        self,
        reason: str = "Provisioning failed",
        session_id: str = None,
        stderr: str = None
    ):
        super().__init__(
            message=reason,
            code="PROVISIONING_ERROR",
            details={
                "session_id": session_id,
                "stderr": stderr
            }
        )


class SessionNotFoundError(SSHBoxError):
    """Raised when session is not found"""
    
    def __init__(self, session_id: str):
        super().__init__(
            message=f"Session not found: {session_id}",
            code="SESSION_NOT_FOUND",
            details={"session_id": session_id}
        )


class SessionAlreadyDestroyedError(SSHBoxError):
    """Raised when attempting to destroy an already destroyed session"""
    
    def __init__(self, session_id: str):
        super().__init__(
            message=f"Session already destroyed: {session_id}",
            code="SESSION_ALREADY_DESTROYED",
            details={"session_id": session_id}
        )


class RateLimitExceededError(SSHBoxError):
    """Raised when rate limit is exceeded"""
    
    def __init__(self, limit: str, retry_after: int = None):
        super().__init__(
            message=f"Rate limit exceeded: {limit}",
            code="RATE_LIMIT_EXCEEDED",
            details={
                "limit": limit,
                "retry_after_seconds": retry_after
            }
        )


class ConfigurationError(SSHBoxError):
    """Raised when configuration is invalid"""
    
    def __init__(self, reason: str = "Invalid configuration", field: str = None):
        super().__init__(
            message=reason,
            code="CONFIGURATION_ERROR",
            details={"field": field}
        )


class DatabaseError(SSHBoxError):
    """Raised when database operation fails"""
    
    def __init__(
        self,
        reason: str = "Database error",
        operation: str = None,
        original_error: str = None
    ):
        super().__init__(
            message=reason,
            code="DATABASE_ERROR",
            details={
                "operation": operation,
                "original_error": original_error
            }
        )


class RecordingError(SSHBoxError):
    """Raised when session recording fails"""
    
    def __init__(
        self,
        reason: str = "Recording error",
        session_id: str = None
    ):
        super().__init__(
            message=reason,
            code="RECORDING_ERROR",
            details={"session_id": session_id}
        )


class PathTraversalError(SSHBoxError):
    """Raised when path traversal attempt is detected"""
    
    def __init__(self, path: str, base_dir: str = None):
        super().__init__(
            message=f"Path traversal attempt detected: {path}",
            code="PATH_TRAVERSAL_ERROR",
            details={
                "path": path,
                "base_dir": base_dir
            }
        )


class CommandInjectionError(SSHBoxError):
    """Raised when command injection attempt is detected"""
    
    def __init__(self, input_value: str, input_name: str = None):
        super().__init__(
            message=f"Command injection attempt detected in {input_name}: {input_value}",
            code="COMMAND_INJECTION_ERROR",
            details={
                "input_name": input_name,
                "input_value": input_value
            }
        )


class ResourceExhaustedError(SSHBoxError):
    """Raised when system resources are exhausted"""
    
    def __init__(self, resource_type: str = "connections", current: int = None, max: int = None):
        super().__init__(
            message=f"Resource exhausted: {resource_type}",
            code="RESOURCE_EXHAUSTED",
            details={
                "resource_type": resource_type,
                "current": current,
                "max": max
            }
        )


class CircuitBreakerOpenError(SSHBoxError):
    """Raised when circuit breaker is open"""
    
    def __init__(self, operation: str, retry_after: int = None):
        super().__init__(
            message=f"Circuit breaker open for operation: {operation}",
            code="CIRCUIT_BREAKER_OPEN",
            details={
                "operation": operation,
                "retry_after_seconds": retry_after
            }
        )


class QuotaExceededError(SSHBoxError):
    """Raised when user quota is exceeded"""
    
    def __init__(
        self,
        quota_type: str = "sessions",
        current: int = None,
        limit: int = None
    ):
        super().__init__(
            message=f"Quota exceeded: {quota_type}",
            code="QUOTA_EXCEEDED",
            details={
                "quota_type": quota_type,
                "current": current,
                "limit": limit
            }
        )


class PolicyDeniedError(SSHBoxError):
    """Raised when policy engine denies a request"""
    
    def __init__(self, reason: str = "Policy denied", policy_path: str = None):
        super().__init__(
            message=reason,
            code="POLICY_DENIED",
            details={"policy_path": policy_path}
        )


class InvalidInputError(SSHBoxError):
    """Raised when input validation fails"""
    
    def __init__(self, field: str, reason: str = "Invalid input", value: str = None):
        super().__init__(
            message=f"Invalid input for {field}: {reason}",
            code="INVALID_INPUT",
            details={
                "field": field,
                "reason": reason,
                "value": value[:100] if value and len(value) > 100 else value
            }
        )


class SSHKeyError(SSHBoxError):
    """Raised when SSH key validation fails"""
    
    def __init__(self, reason: str = "Invalid SSH key"):
        super().__init__(
            message=reason,
            code="SSH_KEY_ERROR"
        )


class NetworkPolicyError(SSHBoxError):
    """Raised when network policy validation fails"""
    
    def __init__(self, reason: str = "Network policy violation", destination: str = None):
        super().__init__(
            message=reason,
            code="NETWORK_POLICY_ERROR",
            details={"destination": destination}
        )
