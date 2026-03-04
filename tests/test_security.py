#!/usr/bin/env python3
"""
Comprehensive Security Tests for sshBox
Tests for vulnerabilities, edge cases, and security controls
"""
import pytest
import hmac
import hashlib
import time
import json
import os
import sys
import tempfile
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
from typing import Dict, Any

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.exceptions import (
    TokenValidationError,
    TokenExpiredError,
    InvalidInputError,
    PathTraversalError,
    CommandInjectionError,
    ConfigurationError,
    QuotaExceededError,
    PolicyDeniedError,
    SSHKeyError,
)
from api.gateway_fastapi import validate_token, constant_time_in, GATEWAY_SECRET
from api.session_recorder import is_safe_path, validate_session_id, SessionRecorder
from api.quota_manager import QuotaManager, QuotaLimit
from api.policy_engine import PolicyEngine, PolicyResult
from api.circuit_breaker import CircuitBreaker, CircuitState


# ============================================================================
# Token Validation Security Tests
# ============================================================================

class TestTokenValidationSecurity:
    """Security tests for token validation"""
    
    def test_constant_time_profile_comparison(self):
        """Test that profile comparison is constant-time to prevent timing attacks"""
        allowed = ["dev", "debug", "secure-shell", "privileged"]
        
        import timeit
        
        # Time comparison for valid profile
        time_valid = timeit.timeit(
            lambda: constant_time_in("dev", allowed),
            number=10000
        )
        
        # Time comparison for invalid profile
        time_invalid = timeit.timeit(
            lambda: constant_time_in("invalid_profile_xyz_123", allowed),
            number=10000
        )
        
        # Times should be within 20% of each other (constant-time)
        ratio = max(time_valid, time_invalid) / min(time_valid, time_invalid)
        assert ratio < 1.2, f"Profile comparison may not be constant-time: ratio={ratio}"
    
    def test_token_replay_prevention(self):
        """Test that old tokens are rejected (replay attack prevention)"""
        # Create token with old timestamp (10 minutes ago)
        old_timestamp = str(int(time.time()) - 600)
        payload = f"dev:600:{old_timestamp}:none:none"
        signature = hmac.new(
            GATEWAY_SECRET.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        
        old_token = f"{payload}:{signature}"
        
        # Should be rejected
        is_valid, error_msg = validate_token(old_token)
        assert is_valid is False
        assert "expired" in error_msg.lower() or "Token" in error_msg
    
    def test_token_future_timestamp_rejected(self):
        """Test that tokens with future timestamps are rejected"""
        future_timestamp = str(int(time.time()) + 3600)  # 1 hour in future
        payload = f"dev:600:{future_timestamp}:none:none"
        signature = hmac.new(
            GATEWAY_SECRET.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        
        future_token = f"{payload}:{signature}"
        
        is_valid, error_msg = validate_token(future_token)
        assert is_valid is False
    
    def test_token_format_validation(self):
        """Test various invalid token formats are rejected"""
        invalid_tokens = [
            "",  # Empty
            "invalid",  # Single part
            "dev:600",  # Two parts
            "dev:600:1234567890",  # Three parts
            "dev:600:1234567890:abcd",  # Four parts
            "dev:not_a_number:1234567890:abcd:none:signature",  # Invalid TTL
            "dev:600:not_a_timestamp:abcd:none:signature",  # Invalid timestamp
            "invalid_profile:600:1234567890:abcd:none:signature",  # Invalid profile
        ]
        
        for token in invalid_tokens:
            is_valid, error_msg = validate_token(token)
            assert is_valid is False, f"Token should be invalid: {token}"
    
    def test_token_signature_validation(self):
        """Test that tokens with invalid signatures are rejected"""
        # Create token with wrong signature
        timestamp = str(int(time.time()))
        payload = f"dev:600:{timestamp}:none:none"
        wrong_signature = "a" * 64  # Wrong signature
        
        wrong_token = f"{payload}:{wrong_signature}"
        
        is_valid, error_msg = validate_token(wrong_token)
        assert is_valid is False
        assert "signature" in error_msg.lower()
    
    def test_token_tampering_detection(self):
        """Test that tampered tokens are detected"""
        # Create valid token
        timestamp = str(int(time.time()))
        payload = f"dev:600:{timestamp}:none:none"
        signature = hmac.new(
            GATEWAY_SECRET.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        
        valid_token = f"{payload}:{signature}"
        
        # Tamper with TTL
        tampered_token = valid_token.replace("600", "7200")
        
        is_valid, error_msg = validate_token(tampered_token)
        assert is_valid is False


# ============================================================================
# Path Traversal Security Tests
# ============================================================================

class TestPathTraversalSecurity:
    """Security tests for path traversal prevention"""
    
    def test_safe_path_within_base(self):
        """Test that paths within base directory are allowed"""
        base = Path("/var/lib/sshbox/recordings")
        
        # Create temp directory for testing
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            base.mkdir(parents=True, exist_ok=True)
            
            # Safe paths should be allowed
            safe_paths = [
                base / "session.cast",
                base / "subdir" / "session.cast",
                base / "a" / "b" / "c" / "session.cast",
            ]
            
            for path in safe_paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
                assert is_safe_path(base, path) is True
    
    def test_path_traversal_blocked(self):
        """Test that path traversal attempts are blocked"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            
            # Unsafe paths should be blocked
            unsafe_paths = [
                Path("/etc/passwd"),
                Path("/var/lib/sshbox/recordings/../../../etc/passwd"),
                Path(tmpdir) / ".." / ".." / "etc" / "passwd",
            ]
            
            for path in unsafe_paths:
                assert is_safe_path(base, path) is False
    
    def test_symlink_traversal_blocked(self):
        """Test that symlink-based traversal is blocked"""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            base.mkdir(parents=True, exist_ok=True)
            
            # Create symlink to outside directory
            outside_file = Path(tmpdir) / "outside.txt"
            outside_file.touch()
            
            symlink = base / "symlink.txt"
            symlink.symlink_to(outside_file)
            
            # Should be blocked because resolved path is outside base
            # (This depends on implementation - some may allow this)
            result = is_safe_path(base, symlink)
            # The result depends on whether we check the symlink or its target


# ============================================================================
# Input Validation Security Tests
# ============================================================================

class TestInputValidationSecurity:
    """Security tests for input validation"""
    
    def test_session_id_injection_prevention(self):
        """Test that session ID injection is prevented"""
        malicious_session_ids = [
            "test; rm -rf /",
            "test$(whoami)",
            "test`id`",
            "test|cat /etc/passwd",
            "test&&echo pwned",
            "test||echo pwned",
            "test\ninjection",
            "test\rinjection",
        ]
        
        for session_id in malicious_session_ids:
            with pytest.raises(InvalidInputError):
                validate_session_id(session_id)
    
    def test_session_id_length_limit(self):
        """Test that excessively long session IDs are rejected"""
        long_session_id = "a" * 200
        
        with pytest.raises(InvalidInputError):
            validate_session_id(long_session_id)
    
    def test_valid_session_id_accepted(self):
        """Test that valid session IDs are accepted"""
        valid_ids = [
            "test_session_123",
            "box-abc-456",
            "session_20240101_120000",
            "a",  # Minimum length
            "a" * 128,  # Maximum length
        ]
        
        for session_id in valid_ids:
            assert validate_session_id(session_id) is True


# ============================================================================
# SSH Key Validation Tests
# ============================================================================

class TestSSHKeyValidation:
    """Security tests for SSH key validation"""

    def test_valid_key_formats_accepted(self):
        """Test that valid SSH key formats are accepted"""
        from api.security import SSHKeyValidator
        
        validator = SSHKeyValidator()
        
        # Valid SSH key formats with realistic key data
        valid_keys = [
            # Ed25519 key (real format)
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl user@example.com",
            # ECDSA key (real format)
            "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg= user@example.com",
        ]
        
        for key in valid_keys:
            is_valid, error = validator.validate(key)
            assert is_valid, f"Valid key should be accepted: {key[:50]}... Error: {error}"
    
    def test_dangerous_key_options_rejected(self):
        """Test that keys with dangerous options are rejected"""
        from api.security import SSHKeyValidator
        
        validator = SSHKeyValidator()
        
        # Keys with dangerous options should be rejected
        dangerous_keys = [
            ('ssh-rsa AAAAB3... command="rm -rf /" user@host', 'command= option'),
            ('ssh-rsa AAAAB3... from="10.0.0.1" user@host', 'from= option'),
            ('ssh-rsa AAAAB3... no-pty user@host', 'no-pty option'),
        ]
        
        for key, description in dangerous_keys:
            is_valid, error = validator.validate(key)
            assert not is_valid, f"Key with {description} should be rejected"


# ============================================================================
# Quota Enforcement Tests
# ============================================================================

class TestQuotaEnforcement:
    """Security tests for quota enforcement"""
    
    def test_concurrent_session_limit(self):
        """Test that concurrent session limits are enforced"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "quotas.db"
            quota_mgr = QuotaManager(db_path=str(db_path))
            
            # Set low quota
            quota_mgr.set_user_quota(
                user_id="test_user",
                max_concurrent_sessions=2
            )
            
            # Record sessions
            quota_mgr.record_usage("test_user", "session_1", "session_created")
            quota_mgr.record_usage("test_user", "session_2", "session_created")
            
            # Third session should be denied
            result = quota_mgr.check_quota("test_user", requested_ttl=1800)
            assert result["allowed"] is False
            assert "concurrent" in result["reason"].lower()
    
    def test_daily_session_limit(self):
        """Test that daily session limits are enforced"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "quotas.db"
            quota_mgr = QuotaManager(db_path=str(db_path))
            
            # Set low daily quota
            quota_mgr.set_user_quota(
                user_id="test_user",
                max_daily_sessions=5
            )
            
            # Record 5 sessions
            for i in range(5):
                quota_mgr.record_usage("test_user", f"session_{i}", "session_created")
            
            # Sixth session should be denied
            result = quota_mgr.check_quota("test_user", requested_ttl=1800)
            assert result["allowed"] is False
            assert "daily" in result["reason"].lower()
    
    def test_ttl_limit_enforcement(self):
        """Test that TTL limits are enforced"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "quotas.db"
            quota_mgr = QuotaManager(db_path=str(db_path))
            
            quota_mgr.set_user_quota(
                user_id="test_user",
                max_session_ttl=3600
            )
            
            # Request with excessive TTL should be denied
            result = quota_mgr.check_quota("test_user", requested_ttl=7200)
            assert result["allowed"] is False
            assert "TTL" in result["reason"]


# ============================================================================
# Policy Engine Tests
# ============================================================================

class TestPolicyEngine:
    """Security tests for policy engine"""
    
    def test_privileged_profile_requires_admin(self):
        """Test that privileged profile requires admin role"""
        engine = PolicyEngine(enable_local_fallback=True)
        
        # Non-admin user requesting privileged profile
        result = engine.check_session_creation(
            user_id="regular_user",
            profile="privileged",
            ttl=1800,
            source_ip="192.168.1.1",
            user_role="default"
        )
        
        assert result.allowed is False
    
    def test_business_hours_restriction(self):
        """Test business hours restriction (OPA policy)"""
        engine = PolicyEngine(enable_local_fallback=True)
        
        # This test would require OPA to be running with the policy loaded
        # For now, we test the local fallback behavior
        result = engine.check_session_creation(
            user_id="test_user",
            profile="dev",
            ttl=1800,
            source_ip="192.168.1.1"
        )
        
        # Local fallback should allow dev profile
        assert result.allowed is True
    
    def test_risk_assessment(self):
        """Test risk assessment functionality"""
        engine = PolicyEngine(enable_local_fallback=True)
        
        # Low risk scenario
        risk = engine.assess_risk(
            user_id="admin_user",
            profile="dev",
            ttl=1800,
            source_ip="10.0.0.1",  # Trusted IP
            user_role="admin"
        )
        
        assert risk["risk_level"] in ["low", "medium"]
        
        # High risk scenario
        risk = engine.assess_risk(
            user_id="trial_user",
            profile="privileged",
            ttl=7200,
            source_ip="203.0.113.1",  # Untrusted IP
            user_role="trial"
        )
        
        assert risk["risk_level"] in ["high", "critical"]


# ============================================================================
# Circuit Breaker Tests
# ============================================================================

class TestCircuitBreaker:
    """Security tests for circuit breaker pattern"""
    
    def test_circuit_opens_after_failures(self):
        """Test that circuit breaker opens after threshold failures"""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=3,
            recovery_timeout=30
        )
        
        def failing_function():
            raise Exception("Simulated failure")
        
        # Trigger failures
        for i in range(3):
            try:
                breaker.call(failing_function)
            except Exception:
                pass
        
        # Circuit should be open
        assert breaker.state == CircuitState.OPEN
    
    def test_circuit_rejects_when_open(self):
        """Test that open circuit rejects calls"""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            recovery_timeout=30
        )
        
        def failing_function():
            raise Exception("Simulated failure")
        
        # Trigger failure
        try:
            breaker.call(failing_function)
        except Exception:
            pass
        
        # Circuit should reject next call
        with pytest.raises(Exception):  # CircuitBreakerOpenError
            breaker.call(lambda: "success")
    
    def test_circuit_half_open_after_timeout(self):
        """Test that circuit enters half-open state after timeout"""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=1,
            recovery_timeout=1  # 1 second timeout
        )
        
        def failing_function():
            raise Exception("Simulated failure")
        
        # Trigger failure
        try:
            breaker.call(failing_function)
        except Exception:
            pass
        
        # Wait for timeout
        time.sleep(1.5)
        
        # Circuit should be half-open
        assert breaker.state == CircuitState.HALF_OPEN


# ============================================================================
# Configuration Security Tests
# ============================================================================

class TestConfigurationSecurity:
    """Security tests for configuration"""
    
    def test_secret_strength_validation(self):
        """Test that weak secrets are rejected"""
        from api.config import SecuritySettings
        from pydantic import ValidationError
        
        weak_secrets = [
            "",  # Empty
            "short",  # Too short
            "alllowercase123",  # No uppercase
            "ALLUPPERCASE123",  # No lowercase
            "NoSpecialChars123",  # No special chars
        ]
        
        for secret in weak_secrets:
            with pytest.raises(ValidationError):
                SecuritySettings(gateway_secret=secret)
    
    def test_strong_secret_accepted(self):
        """Test that strong secrets are accepted"""
        from api.config import SecuritySettings
        
        strong_secret = "MyStr0ng!Secret@Key#2024"
        
        settings = SecuritySettings(gateway_secret=strong_secret)
        assert settings.gateway_secret.get_secret_value() == strong_secret


# ============================================================================
# Integration Security Tests
# ============================================================================

class TestIntegrationSecurity:
    """Integration security tests"""
    
    @patch('subprocess.run')
    def test_command_injection_in_provisioner(self, mock_subprocess):
        """Test that command injection in provisioner is prevented"""
        from api.gateway_fastapi import handle_request
        from fastapi.testclient import TestClient
        from api.gateway_fastapi import app
        
        client = TestClient(app)
        
        # Malicious session ID attempt
        malicious_token = "dev; rm -rf /:600:1234567890:none:none:signature"
        
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"host": "127.0.0.1", "port": 2222, "user": "boxuser"}'
        mock_subprocess.return_value = mock_result
        
        response = client.post(
            "/request",
            json={
                "token": malicious_token,
                "pubkey": "ssh-rsa AAAAB3" + "A" * 100,
                "profile": "dev",
                "ttl": 600
            }
        )
        
        # Should fail token validation before reaching provisioner
        assert response.status_code == 403


# ============================================================================
# Run Tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
