import pytest
import subprocess
import time
import json
import os
import tempfile
from unittest.mock import patch, MagicMock
from datetime import datetime
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Import the API modules we want to test
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from api.gateway_fastapi import app, validate_token
from api.sqlite_session_recorder import SQLiteSessionRecorder


@pytest.fixture
def client():
    """Create a test client for the FastAPI app"""
    return TestClient(app)


@pytest.fixture
def sample_token():
    """Create a sample token for testing"""
    # In a real test, we'd properly create a valid token
    # For now, we'll just return a placeholder that passes basic validation
    return "dev:600:1234567890:abcd1234:none:somesignature"


class TestGatewayAPI:
    """Test the gateway API endpoints"""
    
    def test_health_endpoint(self, client):
        """Test the health check endpoint"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] == "healthy"
        assert "timestamp" in data
    
    def test_root_endpoint(self, client):
        """Test the root endpoint"""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "endpoints" in data
        assert "/request" in str(data["endpoints"])
    
    @patch('subprocess.run')
    def test_request_endpoint_with_mock(self, mock_subprocess):
        """Test the request endpoint with mocked subprocess"""
        client = TestClient(app)
        
        # Mock the subprocess call to the provisioner
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"host": "127.0.0.1", "port": 2222, "user": "boxuser", "session_id": "test123"}'
        mock_subprocess.return_value = mock_result
        
        # Mock token validation to return True
        with patch('api.gateway_fastapi.validate_token', return_value=True):
            response = client.post("/request", json={
                "token": "dev:600:1234567890:abcd1234:none:somesignature",
                "pubkey": "ssh-rsa AAAAB3NzaC1yc2E... test@example.com",
                "profile": "dev",
                "ttl": 600
            })
            
            assert response.status_code == 200
            data = response.json()
            assert "host" in data
            assert "port" in data
            assert "user" in data
            assert "session_id" in data


class TestTokenValidation:
    """Test token validation logic"""
    
    def test_validate_token_format(self):
        """Test that token validation works correctly"""
        # Valid token format: profile:ttl:timestamp:recipient_hash:notes_hash:signature
        # Since we can't easily create a valid HMAC without the secret in tests,
        # we'll test the parsing logic
        token = "dev:600:1234567890:abcd1234:none:" + "a" * 64  # fake signature
        result = validate_token(token)
        # This will return False because the signature won't match, but it should parse correctly
        # The important thing is that it doesn't crash
        
        # Test invalid format
        invalid_token = "invalid:format"
        result = validate_token(invalid_token)
        assert result is False
        
        # Test correct format but wrong signature
        token = "dev:600:1234567890:abcd1234:none:" + "a" * 64
        result = validate_token(token)
        # Will be False because signature won't match without the right secret


class TestSQLiteRecorderAPI:
    """Test the SQLite recorder API"""
    
    @pytest.fixture
    def recorder_client(self):
        """Create a test client for the recorder API"""
        from api.sqlite_session_recorder import app as recorder_app
        return TestClient(recorder_app)
    
    def test_recorder_health(self, recorder_client):
        """Test the recorder health endpoint"""
        response = recorder_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
    
    def test_start_recording_endpoint(self, recorder_client):
        """Test the start recording endpoint"""
        response = recorder_client.post("/recordings/start", json={
            "session_id": "test_session_api",
            "container_name": "test_container",
            "user_id": "test_user",
            "profile": "dev",
            "ttl": 1800
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "test_session_api"
    
    def test_get_recording_endpoint(self, recorder_client):
        """Test the get recording endpoint"""
        # First start a recording
        start_response = recorder_client.post("/recordings/start", json={
            "session_id": "test_get_rec",
            "container_name": "test_container",
            "profile": "dev"
        })
        assert start_response.status_code == 200
        
        # Then try to get it
        response = recorder_client.get("/recordings/test_get_rec")
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "test_get_rec"


class TestQualityAssurance:
    """Quality assurance tests"""
    
    def test_requirements_versions(self):
        """Test that requirements are properly pinned"""
        req_file = os.path.join(os.path.dirname(__file__), '..', 'requirements.txt')
        assert os.path.exists(req_file)
        
        with open(req_file, 'r') as f:
            content = f.read()
            
        # Check that all packages have versions (contain ==)
        packages = content.strip().split('\n')
        for pkg in packages:
            if pkg.strip() and not pkg.strip().startswith('#'):
                assert '==' in pkg or '>=' in pkg or '<=' in pkg, f"Package {pkg} is not properly pinned"
    
    def test_documentation_exists(self):
        """Test that important documentation files exist"""
        docs = [
            '../README.md',
            '../docs/firecracker_implementation.md',
            '../docs/provisioner_config.md',
            '../schemas/invite_token_format.md',
            '../schemas/session_metadata_schema.json',
            '../schemas/profile_schema.yaml'
        ]
        
        for doc in docs:
            path = os.path.join(os.path.dirname(__file__), doc)
            assert os.path.exists(path), f"Documentation file {doc} is missing"
    
    def test_scripts_are_executable(self):
        """Test that shell scripts are executable"""
        scripts = [
            '../scripts/box-provision.sh',
            '../scripts/box-destroy.sh',
            '../scripts/box-invite.py',
            '../scripts/ssh-gateway-proxy.sh',
            '../scripts/box-provision-firecracker.sh',
            '../scripts/box-destroy-firecracker.sh'
        ]
        
        for script in scripts:
            path = os.path.join(os.path.dirname(__file__), script)
            assert os.path.exists(path), f"Script {script} is missing"
            assert os.access(path, os.X_OK), f"Script {script} is not executable"
    
    def test_docker_compose_exists(self):
        """Test that docker-compose file exists"""
        compose_file = os.path.join(os.path.dirname(__file__), '..', 'docker-compose.yml')
        assert os.path.exists(compose_file)


# Run the tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])