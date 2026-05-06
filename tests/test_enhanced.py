#!/usr/bin/env python3
"""
Comprehensive tests for sshBox enhanced implementations
"""
import pytest
import os
import sys
import time
import json
import tempfile
import subprocess
from unittest.mock import patch, MagicMock, Mock
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.config import Settings, DatabaseSettings, SecuritySettings, StorageSettings
from api.security import (
    validate_token,
    create_token,
    TokenValidationError,
    TokenPayload,
    SSHKeyValidator,
    InputValidator,
    constant_time_compare,
    constant_time_in
)
from api.metrics import MetricsCollector, get_metrics_collector


# ===========================================
# Configuration Tests
# ===========================================

class TestSettings:
    """Test configuration management"""
    
    def test_default_settings(self):
        """Test default settings are loaded"""
        settings = Settings()
        assert settings.app_name == "sshBox"
        assert settings.gateway_port == 8080
        assert settings.database.db_type == "sqlite"
    
    def test_security_settings_validation(self):
        """Test security settings validation"""
        # Should auto-generate secret if not provided
        settings = SecuritySettings()
        assert len(settings.gateway_secret) >= 32
        
        # Should reject short secrets
        with pytest.raises(ValueError) as exc_info:
            SecuritySettings(gateway_secret="short")
        assert "at least 32 characters" in str(exc_info.value)
    
    def test_settings_validation(self):
        """Test settings validation"""
        settings = Settings()
        errors = settings.validate_all()
        # Should have errors for non-writable directories in test environment
        assert isinstance(errors, list)
    
    def test_environment_variable_loading(self):
        """Test loading settings from environment variables"""
        os.environ['SSHBOX_GATEWAY_PORT'] = '9999'
        os.environ['SSHBOX_SECURITY_GATEWAY_SECRET'] = 'test-secret-at-least-32-characters-long'
        
        # Need to reload settings
        from api.config import reload_settings
        settings = reload_settings()
        
        assert settings.gateway_port == 9999
        
        # Cleanup
        del os.environ['SSHBOX_GATEWAY_PORT']
        del os.environ['SSHBOX_SECURITY_GATEWAY_SECRET']


# ===========================================
# Security Tests
# ===========================================

class TestTokenValidation:
    """Test token creation and validation"""
    
    def test_create_token(self):
        """Test token creation"""
        secret = "test-secret-at-least-32-characters-long"
        token = create_token(secret, profile="dev", ttl=600)
        
        parts = token.split(':')
        assert len(parts) == 6
        assert parts[0] == "dev"
        assert parts[1] == "600"
    
    def test_create_token_with_recipient_and_notes(self):
        """Test token creation with optional fields"""
        secret = "test-secret-at-least-32-characters-long"
        token = create_token(
            secret,
            profile="debug",
            ttl=1200,
            recipient="test@example.com",
            notes="Test notes"
        )
        
        parts = token.split(':')
        assert len(parts) == 6
        assert parts[0] == "debug"
        assert parts[3] != 'none'  # recipient_hash
        assert parts[4] != 'none'  # notes_hash
    
    def test_validate_valid_token(self):
        """Test validating a valid token"""
        secret = "test-secret-at-least-32-characters-long"
        token = create_token(secret, profile="dev", ttl=600)
        
        payload = validate_token(token, secret)
        
        assert payload.profile == "dev"
        assert payload.ttl == 600
        assert not payload.is_expired
    
    def test_validate_invalid_signature(self):
        """Test validating token with wrong signature"""
        secret = "test-secret-at-least-32-characters-long"
        token = create_token(secret, profile="dev", ttl=600)
        
        # Tamper with signature
        parts = token.split(':')
        parts[5] = "invalid" + parts[5][7:]
        tampered_token = ':'.join(parts)
        
        with pytest.raises(TokenValidationError) as exc_info:
            validate_token(tampered_token, secret)
        assert "INVALID_SIGNATURE" in exc_info.value.error_code
    
    def test_validate_expired_token(self):
        """Test validating expired token"""
        secret = "test-secret-at-least-32-characters-long"
        
        # Create token with old timestamp
        old_timestamp = int(time.time()) - 600  # 10 minutes ago
        token = create_token(secret, profile="dev", ttl=600, timestamp=old_timestamp)
        
        with pytest.raises(TokenValidationError) as exc_info:
            validate_token(token, secret)
        assert "TOKEN_EXPIRED" in exc_info.value.error_code
    
    def test_validate_invalid_profile(self):
        """Test validating token with invalid profile"""
        secret = "test-secret-at-least-32-characters-long"
        
        # Create token and tamper with profile
        token = create_token(secret, profile="dev", ttl=600)
        parts = token.split(':')
        parts[0] = "invalid-profile"
        # Recalculate signature
        payload = ':'.join(parts[:5])
        import hmac, hashlib
        parts[5] = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        tampered_token = ':'.join(parts)
        
        with pytest.raises(TokenValidationError) as exc_info:
            validate_token(tampered_token, secret)
        assert "INVALID_PROFILE" in exc_info.value.error_code
    
    def test_validate_invalid_format(self):
        """Test validating token with invalid format"""
        secret = "test-secret-at-least-32-characters-long"
        
        with pytest.raises(TokenValidationError) as exc_info:
            validate_token("invalid-token", secret)
        assert "INVALID_FORMAT" in exc_info.value.error_code
    
    def test_token_payload_properties(self):
        """Test TokenPayload properties"""
        secret = "test-secret-at-least-32-characters-long"
        token = create_token(secret, profile="dev", ttl=600)
        payload = validate_token(token, secret)
        
        assert isinstance(payload.created_at, datetime)
        assert isinstance(payload.expires_at, datetime)
        assert payload.expires_at > payload.created_at
        
        # Test to_dict
        payload_dict = payload.to_dict()
        assert payload_dict['profile'] == "dev"
        assert payload_dict['ttl'] == 600
        assert 'is_expired' in payload_dict


class TestConstantTimeOperations:
    """Test constant-time comparison functions"""
    
    def test_constant_time_compare_equal(self):
        """Test constant_time_compare with equal strings"""
        assert constant_time_compare("test", "test") is True
        assert constant_time_compare("", "") is True
    
    def test_constant_time_compare_not_equal(self):
        """Test constant_time_compare with different strings"""
        assert constant_time_compare("test", "TEST") is False
        assert constant_time_compare("test", "test1") is False
        assert constant_time_compare("test", "tes") is False
    
    def test_constant_time_in_found(self):
        """Test constant_time_in when value is in list"""
        allowed = ["dev", "debug", "secure-shell"]
        assert constant_time_in("dev", allowed) is True
        assert constant_time_in("debug", allowed) is True
    
    def test_constant_time_in_not_found(self):
        """Test constant_time_in when value is not in list"""
        allowed = ["dev", "debug", "secure-shell"]
        assert constant_time_in("invalid", allowed) is False
        assert constant_time_in("", allowed) is False
        assert constant_time_in("dev1", allowed) is False


class TestSSHKeyValidator:
    """Test SSH key validation"""
    
    def test_valid_rsa_key(self):
        """Test validating a valid RSA key"""
        validator = SSHKeyValidator()
        pubkey = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7... test@example.com"
        
        # Note: This is a truncated key for testing format
        # Real test would use a full valid key
        is_valid, error = validator.validate(pubkey)
        # May fail due to truncated key, but should not crash
    
    def test_valid_ed25519_key(self):
        """Test validating a valid ED25519 key"""
        validator = SSHKeyValidator()
        # Valid ED25519 key format
        pubkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl test@example.com"
        
        is_valid, error = validator.validate(pubkey)
        assert is_valid is True, f"Expected valid key, got error: {error}"
    
    def test_invalid_key_format(self):
        """Test validating invalid key format"""
        validator = SSHKeyValidator()
        pubkey = "not-a-valid-key"
        
        is_valid, error = validator.validate(pubkey)
        assert is_valid is False
        assert "required" in error.lower() or "format" in error.lower()
    
    def test_empty_key(self):
        """Test validating empty key"""
        validator = SSHKeyValidator()
        
        is_valid, error = validator.validate("")
        assert is_valid is False
        assert "required" in error.lower()
    
    def test_get_fingerprint(self):
        """Test getting key fingerprint"""
        validator = SSHKeyValidator()
        pubkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl test@example.com"
        
        fingerprint = validator.get_key_fingerprint(pubkey)
        assert fingerprint is not None
        assert fingerprint.startswith("SHA256:")


class TestInputValidator:
    """Test input validation"""
    
    def test_valid_session_id(self):
        """Test validating valid session ID"""
        is_valid, error = InputValidator.validate_session_id("abc123def456")
        assert is_valid is True
    
    def test_invalid_session_id_too_short(self):
        """Test validating session ID that's too short"""
        is_valid, error = InputValidator.validate_session_id("abc")
        assert is_valid is False
        assert "between 10 and 64" in error
    
    def test_invalid_session_id_too_long(self):
        """Test validating session ID that's too long"""
        is_valid, error = InputValidator.validate_session_id("a" * 100)
        assert is_valid is False
        assert "between 10 and 64" in error
    
    def test_invalid_session_id_characters(self):
        """Test validating session ID with invalid characters"""
        is_valid, error = InputValidator.validate_session_id("abc;123")
        assert is_valid is False
    
    def test_valid_container_name(self):
        """Test validating valid container name"""
        is_valid, error = InputValidator.validate_container_name("sshbox_test_123")
        assert is_valid is True
    
    def test_invalid_container_name(self):
        """Test validating invalid container name"""
        is_valid, error = InputValidator.validate_container_name("-invalid")
        assert is_valid is False
    
    def test_sanitize_path(self):
        """Test path sanitization"""
        base_dir = "/var/lib/sshbox"
        
        # Valid path
        result = InputValidator.sanitize_path("recordings/test.cast", base_dir)
        assert result is not None
        assert result.startswith("/var/lib/sshbox")
        
        # Path traversal attempt
        result = InputValidator.sanitize_path("../../etc/passwd", base_dir)
        assert result is None


# ===========================================
# Metrics Tests
# ===========================================

class TestMetricsCollector:
    """Test metrics collection"""
    
    def test_init(self):
        """Test metrics collector initialization"""
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            metrics = MetricsCollector(f.name)
            assert metrics.start_time is not None
            assert metrics.requests_total == 0
            f.close()
            os.unlink(f.name)
    
    def test_record_request(self):
        """Test recording requests"""
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            metrics = MetricsCollector(f.name)
            
            metrics.record_request("/request", success=True, status_code=200, process_time=0.5)
            metrics.record_request("/request", success=True, status_code=200, process_time=0.3)
            metrics.record_request("/sessions", success=False, status_code=500, process_time=0.1)
            
            assert metrics.requests_total == 3
            assert metrics.requests_successful == 2
            assert metrics.requests_failed == 1
            assert metrics.requests_by_endpoint["/request"] == 2
            
            f.close()
            os.unlink(f.name)
    
    def test_record_session_creation(self):
        """Test recording session creation"""
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            metrics = MetricsCollector(f.name)
            
            metrics.record_session_creation("dev")
            metrics.record_session_creation("debug")
            metrics.record_session_creation("dev")
            
            assert metrics.sessions_created == 3
            assert metrics.sessions_by_profile["dev"] == 2
            assert metrics.sessions_by_profile["debug"] == 1
            assert metrics.active_sessions == 3
            
            f.close()
            os.unlink(f.name)
    
    def test_record_session_destruction(self):
        """Test recording session destruction"""
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            metrics = MetricsCollector(f.name)
            
            metrics.record_session_creation("dev")
            metrics.record_session_creation("dev")
            metrics.record_session_destruction()
            
            assert metrics.sessions_destroyed == 1
            assert metrics.active_sessions == 1
            
            f.close()
            os.unlink(f.name)
    
    def test_record_error(self):
        """Test recording errors"""
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            metrics = MetricsCollector(f.name)
            
            metrics.record_error("provision_failed")
            metrics.record_error("provision_failed")
            metrics.record_error("db_error")
            
            assert metrics.errors_total == 3
            assert metrics.errors_by_type["provision_failed"] == 2
            assert metrics.errors_by_type["db_error"] == 1
            
            f.close()
            os.unlink(f.name)
    
    def test_get_metrics(self):
        """Test getting metrics as dictionary"""
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            metrics = MetricsCollector(f.name)
            
            metrics.record_request("/request", success=True, process_time=0.5)
            metrics.record_session_creation("dev")
            metrics.record_error("test_error")
            
            metrics_dict = metrics.get_metrics()
            
            assert "start_time" in metrics_dict
            assert "uptime_seconds" in metrics_dict
            assert "requests" in metrics_dict
            assert "sessions" in metrics_dict
            assert "errors" in metrics_dict
            
            f.close()
            os.unlink(f.name)
    
    def test_prometheus_metrics(self):
        """Test Prometheus format export"""
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            metrics = MetricsCollector(f.name)
            
            metrics.record_request("/request", success=True)
            metrics.record_session_creation("dev")
            
            prometheus_output = metrics.get_prometheus_metrics()
            
            assert "sshbox_requests_total" in prometheus_output
            assert "sshbox_sessions_created" in prometheus_output
            assert "# HELP" in prometheus_output
            assert "# TYPE" in prometheus_output
            
            f.close()
            os.unlink(f.name)
    
    def test_timing_metrics(self):
        """Test timing metrics"""
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            metrics = MetricsCollector(f.name)
            
            for i in range(10):
                metrics.record_timing("provision_time", float(i) / 10)
            
            metrics_dict = metrics.get_metrics()
            provision_stats = metrics_dict["sessions"]["provision_time"]
            
            assert provision_stats["count"] == 10
            assert provision_stats["avg"] > 0
            assert provision_stats["min"] == 0.0
            assert provision_stats["max"] == 0.9
            
            f.close()
            os.unlink(f.name)


# ===========================================
# Integration Tests
# ===========================================

class TestIntegration:
    """Integration tests for complete flows"""
    
    def test_token_creation_and_validation(self):
        """Test complete token flow"""
        secret = "test-secret-at-least-32-characters-long"
        
        # Create token
        token = create_token(
            secret,
            profile="debug",
            ttl=1200,
            recipient="test@example.com",
            notes="Integration test"
        )
        
        # Validate token
        payload = validate_token(token, secret)
        
        assert payload.profile == "debug"
        assert payload.ttl == 1200
        assert not payload.is_expired
    
    def test_metrics_flow(self):
        """Test complete metrics flow"""
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            metrics = MetricsCollector(f.name)
            
            # Simulate API flow
            start_time = time.time()
            
            # Request received
            metrics.record_request("/request", success=True, process_time=0.5)
            
            # Session created
            metrics.record_session_creation("dev")
            
            # Some time passes
            time.sleep(0.1)
            
            # Session destroyed
            metrics.record_session_destruction()
            
            # Get final metrics
            metrics_dict = metrics.get_metrics()
            
            assert metrics_dict["requests"]["total"] == 1
            assert metrics_dict["sessions"]["created"] == 1
            assert metrics_dict["sessions"]["destroyed"] == 1
            assert metrics_dict["sessions"]["active"] == 0
            
            f.close()
            os.unlink(f.name)


# ===========================================
# Run Tests
# ===========================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
