#!/usr/bin/env python3
"""
Security utilities for sshBox
Provides secure token validation, SSH key validation, and other security functions
"""
import hmac
import hashlib
import time
import re
from typing import Optional, Tuple, List, Dict, Any
from datetime import datetime
import logging

from api.config import get_settings, SecuritySettings

logger = logging.getLogger(__name__)


class TokenValidationError(Exception):
    """Raised when token validation fails"""
    def __init__(self, message: str, error_code: str = "INVALID_TOKEN"):
        super().__init__(message)
        self.error_code = error_code


class TokenPayload:
    """Represents a validated token payload"""
    def __init__(
        self,
        profile: str,
        ttl: int,
        timestamp: int,
        recipient_hash: str,
        notes_hash: str,
        signature: str
    ):
        self.profile = profile
        self.ttl = ttl
        self.timestamp = timestamp
        self.recipient_hash = recipient_hash
        self.notes_hash = notes_hash
        self.signature = signature
    
    @property
    def created_at(self) -> datetime:
        """Get token creation time"""
        return datetime.fromtimestamp(self.timestamp)
    
    @property
    def expires_at(self) -> datetime:
        """Get token expiration time"""
        return datetime.fromtimestamp(self.timestamp + self.ttl)
    
    @property
    def is_expired(self) -> bool:
        """Check if token is expired"""
        return time.time() > self.timestamp + self.ttl
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "profile": self.profile,
            "ttl": self.ttl,
            "timestamp": self.timestamp,
            "recipient_hash": self.recipient_hash,
            "notes_hash": self.notes_hash,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "is_expired": self.is_expired
        }


def constant_time_compare(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing attacks"""
    return hmac.compare_digest(a.encode('utf-8'), b.encode('utf-8'))


def constant_time_in(value: str, allowed_list: List[str]) -> bool:
    """
    Constant-time membership check to prevent timing attacks.
    Always compares against all items regardless of match position.
    """
    if not value or not allowed_list:
        return False
    
    result = 0
    value_bytes = value.encode('utf-8')
    for item in allowed_list:
        result |= hmac.compare_digest(value_bytes, item.encode('utf-8'))
    return bool(result)


def create_token(
    secret: str,
    profile: str,
    ttl: int,
    recipient: Optional[str] = None,
    notes: Optional[str] = None,
    timestamp: Optional[int] = None
) -> str:
    """
    Create a signed invite token with HMAC signature.
    
    Format: profile:ttl:timestamp:recipient_hash:notes_hash:signature
    
    Args:
        secret: Secret key for signing
        profile: Box profile (dev, debug, secure-shell, privileged)
        ttl: Time-to-live in seconds
        recipient: Optional recipient identifier
        notes: Optional notes
        timestamp: Optional timestamp (defaults to current time)
    
    Returns:
        Signed token string
    """
    if timestamp is None:
        timestamp = int(time.time())
    
    # Create hashes for recipient and notes
    recipient_hash = 'none'
    if recipient:
        recipient_hash = hashlib.sha256(recipient.encode()).hexdigest()[:12]
    
    notes_hash = 'none'
    if notes:
        notes_hash = hashlib.sha256(notes.encode()).hexdigest()[:12]
    
    # Create payload
    payload = f"{profile}:{ttl}:{timestamp}:{recipient_hash}:{notes_hash}"
    
    # Create HMAC signature
    signature = hmac.new(
        secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return f"{payload}:{signature}"


def validate_token(token: str, secret: Optional[str] = None, settings: Optional[SecuritySettings] = None) -> TokenPayload:
    """
    Validate an invite token with comprehensive security checks.
    
    Args:
        token: Token string to validate
        secret: Secret key for validation (uses settings if not provided)
        settings: Security settings (uses global settings if not provided)
    
    Returns:
        TokenPayload if valid
    
    Raises:
        TokenValidationError: If token is invalid
    """
    if settings is None:
        settings = get_settings().security
    
    if secret is None:
        secret = settings.gateway_secret

    if hasattr(secret, 'get_secret_value'):
        secret = secret.get_secret_value()
    
    # Parse token
    try:
        parts = token.split(':')
        if len(parts) != 6:
            logger.warning(f"Invalid token format: expected 6 parts, got {len(parts)}")
            raise TokenValidationError("Invalid token format", "INVALID_FORMAT")
        
        profile, ttl_str, timestamp_str, recipient_hash, notes_hash, signature = parts
        
    except ValueError as e:
        logger.warning(f"Failed to parse token: {e}")
        raise TokenValidationError("Invalid token format", "INVALID_FORMAT")
    
    # Validate TTL is numeric
    try:
        ttl = int(ttl_str)
        if ttl <= 0:
            raise ValueError("TTL must be positive")
    except ValueError:
        logger.warning(f"Invalid TTL in token: {ttl_str}")
        raise TokenValidationError("Invalid TTL", "INVALID_TTL")
    
    # Validate TTL is within allowed range
    max_ttl = getattr(settings, 'max_ttl', 7200)
    min_ttl = 60
    if ttl < min_ttl or ttl > max_ttl:
        logger.warning(f"TTL {ttl} outside allowed range [{min_ttl}, {max_ttl}]")
        raise TokenValidationError(
            f"TTL must be between {min_ttl} and {max_ttl} seconds",
            "INVALID_TTL_RANGE"
        )
    
    # Validate timestamp is numeric
    try:
        timestamp = int(timestamp_str)
    except ValueError:
        logger.warning(f"Invalid timestamp in token: {timestamp_str}")
        raise TokenValidationError("Invalid timestamp", "INVALID_TIMESTAMP")
    
    # Check token age (prevent replay attacks)
    current_time = int(time.time())
    token_age = current_time - timestamp
    
    if token_age < 0:
        logger.warning(f"Token timestamp is in the future: {timestamp}")
        raise TokenValidationError("Token timestamp is in the future", "INVALID_TIMESTAMP")
    
    if token_age > settings.token_max_age:
        logger.warning(f"Token too old: age={token_age}s, max={settings.token_max_age}s")
        raise TokenValidationError("Token has expired", "TOKEN_EXPIRED")
    
    # Validate profile using constant-time comparison
    if not constant_time_in(profile, settings.allowed_profiles):
        logger.warning(f"Invalid profile in token: {profile}")
        raise TokenValidationError("Invalid profile", "INVALID_PROFILE")
    
    # Verify signature using constant-time comparison
    expected_payload = f"{profile}:{ttl_str}:{timestamp_str}:{recipient_hash}:{notes_hash}"
    expected_signature = hmac.new(
        secret.encode('utf-8'),
        expected_payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    if not constant_time_compare(signature, expected_signature):
        logger.warning("Invalid token signature")
        raise TokenValidationError("Invalid token signature", "INVALID_SIGNATURE")
    
    # Create and return payload
    return TokenPayload(
        profile=profile,
        ttl=ttl,
        timestamp=timestamp,
        recipient_hash=recipient_hash,
        notes_hash=notes_hash,
        signature=signature
    )


class SSHKeyValidator:
    """Validates SSH public keys"""
    
    def __init__(self, settings: Optional[SecuritySettings] = None):
        if settings is None:
            settings = get_settings().security
        self.settings = settings
        
        # SSH key type patterns
        self.key_patterns = {
            'ssh-rsa': re.compile(r'^ssh-rsa\s+AAAA[0-9A-Za-z+/]+=*\s+\S+'),
            'ssh-ed25519': re.compile(r'^ssh-ed25519\s+AAAA[0-9A-Za-z+/]+=*\s+\S+'),
            'ecdsa-sha2-nistp256': re.compile(r'^ecdsa-sha2-nistp256\s+AAAA[0-9A-Za-z+/]+=*\s+\S+'),
            'ecdsa-sha2-nistp384': re.compile(r'^ecdsa-sha2-nistp384\s+AAAA[0-9A-Za-z+/]+=*\s+\S+'),
            'ecdsa-sha2-nistp521': re.compile(r'^ecdsa-sha2-nistp521\s+AAAA[0-9A-Za-z+/]+=*\s+\S+'),
            'sk-ecdsa-sha2-nistp256@openssh.com': re.compile(r'^sk-ecdsa-sha2-nistp256@openssh\.com\s+\S+\s+\S+'),
            'sk-ssh-ed25519@openssh.com': re.compile(r'^sk-ssh-ed25519@openssh\.com\s+\S+\s+\S+'),
        }
    
    def validate(self, pubkey: str) -> Tuple[bool, str]:
        """
        Validate SSH public key.
        
        Args:
            pubkey: SSH public key string
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not pubkey or not isinstance(pubkey, str):
            return False, "Public key is required"
        
        pubkey = pubkey.strip()
        
        if len(pubkey) < 64:
            return False, "Public key too short"
        
        # Check key type
        key_type = pubkey.split()[0] if pubkey.split() else ""
        
        if not constant_time_in(key_type, self.settings.allowed_key_types):
            return False, f"Key type '{key_type}' not allowed. Allowed types: {', '.join(self.settings.allowed_key_types)}"
        
        # Validate format based on key type
        if key_type in self.key_patterns:
            if not self.key_patterns[key_type].match(pubkey):
                return False, f"Invalid {key_type} key format"
        else:
            # Generic validation for unknown but allowed types
            parts = pubkey.split()
            if len(parts) < 2:
                return False, "Invalid key format: expected key-type base64-key [comment]"
        
        # For RSA keys, check key size
        if key_type == 'ssh-rsa':
            try:
                import base64
                key_data = base64.b64decode(pubkey.split()[1])
                # RSA key format: length(4) + "ssh-rsa"(7) + length(4) + e + length(4) + n
                if len(key_data) < 277:  # Minimum for 2048-bit RSA
                    return False, f"RSA key must be at least {self.settings.ssh_min_rsa_bits} bits"
            except Exception:
                return False, "Invalid RSA key encoding"
        
        # Check for dangerous key options
        dangerous_options = ['command=', 'no-pty', 'permitopen=', 'from=', 'restrict']
        for option in dangerous_options:
            if option in pubkey:
                logger.warning(f"SSH key contains potentially dangerous option: {option}")
                # For now, we allow these but log them. Could be made configurable.
        
        return True, ""
    
    def get_key_fingerprint(self, pubkey: str) -> Optional[str]:
        """
        Get SHA256 fingerprint of SSH public key.
        
        Args:
            pubkey: SSH public key string
        
        Returns:
            Fingerprint string or None if invalid
        """
        try:
            import base64
            parts = pubkey.strip().split()
            if len(parts) < 2:
                return None
            
            key_data = base64.b64decode(parts[1])
            fingerprint = hashlib.sha256(key_data).digest()
            
            # Format as SHA256:XX:XX:XX:...
            return "SHA256:" + ":".join(f"{b:02x}" for b in fingerprint)
        except Exception:
            return None


class InputValidator:
    """Validates user inputs for security"""
    
    # Safe identifier pattern: alphanumeric, dash, underscore
    SAFE_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')
    
    # Session ID pattern (more restrictive)
    SESSION_ID_PATTERN = re.compile(r'^[a-zA-Z0-9]{10,64}$')
    
    @classmethod
    def validate_session_id(cls, session_id: str) -> Tuple[bool, str]:
        """
        Validate session ID format.
        
        Args:
            session_id: Session ID to validate
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not session_id:
            return False, "Session ID is required"
        
        if not isinstance(session_id, str):
            return False, "Session ID must be a string"
        
        if len(session_id) < 10 or len(session_id) > 64:
            return False, "Session ID must be between 10 and 64 characters"
        
        if not cls.SESSION_ID_PATTERN.match(session_id):
            return False, "Session ID contains invalid characters"
        
        return True, ""
    
    @classmethod
    def validate_container_name(cls, name: str) -> Tuple[bool, str]:
        """
        Validate container name format (Docker rules).
        
        Args:
            name: Container name to validate
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not name:
            return False, "Container name is required"
        
        if not isinstance(name, str):
            return False, "Container name must be a string"
        
        # Docker container name rules
        if len(name) > 128:
            return False, "Container name too long (max 128 characters)"
        
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]*$', name):
            return False, "Container name must start with alphanumeric character"
        
        return True, ""
    
    @classmethod
    def sanitize_path(cls, path: str, base_dir: str) -> Optional[str]:
        """
        Sanitize path to prevent directory traversal.
        
        Args:
            path: Path to sanitize
            base_dir: Base directory that path must be within
        
        Returns:
            Sanitized absolute path or None if invalid
        """
        try:
            from pathlib import Path
            
            # Resolve to absolute path
            resolved = Path(base_dir).resolve() / path
            resolved = resolved.resolve()
            
            # Check if within base directory
            base_resolved = Path(base_dir).resolve()
            try:
                resolved.relative_to(base_resolved)
                return str(resolved)
            except ValueError:
                logger.warning(f"Path traversal attempt: {path} outside {base_dir}")
                return None
                
        except Exception as e:
            logger.error(f"Error sanitizing path: {e}")
            return None
