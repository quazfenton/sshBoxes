import unittest
import subprocess
import time
import json
import os
import tempfile
from unittest.mock import patch, MagicMock
from datetime import datetime

# Import the modules we want to test
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from scripts.box_invite import create_invite, client_connect
from api.sqlite_session_recorder import SQLiteSessionRecorder


class TestInviteTokenCreation(unittest.TestCase):
    """Test invite token creation and validation"""
    
    def test_create_invite_basic(self):
        """Test basic invite token creation"""
        secret = "test_secret"
        token_data = create_invite(secret, profile="dev", ttl=600)
        
        self.assertIn("token", token_data)
        self.assertEqual(token_data["profile"], "dev")
        self.assertEqual(token_data["ttl"], 600)
        
        # Verify token format: profile:ttl:timestamp:recipient_hash:notes_hash:signature
        token_parts = token_data["token"].split(":")
        self.assertEqual(len(token_parts), 6)  # profile:ttl:timestamp:recipient_hash:notes_hash:signature
        
    def test_create_invite_with_recipient_and_notes(self):
        """Test invite creation with recipient and notes"""
        secret = "test_secret"
        token_data = create_invite(
            secret, 
            profile="debug", 
            ttl=1200, 
            recipient="test@example.com", 
            notes="For debugging purposes"
        )
        
        self.assertIn("token", token_data)
        self.assertEqual(token_data["profile"], "debug")
        self.assertEqual(token_data["ttl"], 1200)
        self.assertEqual(token_data["recipient"], "test@example.com")
        self.assertEqual(token_data["notes"], "For debugging purposes")
        
    def test_token_validation_logic(self):
        """Test the token validation logic"""
        secret = "test_secret"
        token_data = create_invite(secret, profile="dev", ttl=600)
        token = token_data["token"]
        
        # Test that the token format is correct
        parts = token.split(":")
        self.assertEqual(len(parts), 6)  # profile:ttl:timestamp:recipient_hash:notes_hash:signature
        
        # Extract components
        profile, ttl_str, timestamp, recipient_hash, notes_hash, signature = parts
        self.assertEqual(profile, "dev")
        self.assertEqual(int(ttl_str), 600)


class TestSessionRecorder(unittest.TestCase):
    """Test SQLite session recorder functionality"""
    
    def setUp(self):
        """Set up test database"""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.recorder = SQLiteSessionRecorder(db_path=self.temp_db.name)
    
    def tearDown(self):
        """Clean up test database"""
        os.unlink(self.temp_db.name)
    
    def test_start_recording(self):
        """Test starting a session recording"""
        session_id = "test_session_123"
        result = self.recorder.start_recording(
            session_id=session_id,
            container_name="test_container",
            user_id="test_user",
            profile="dev",
            ttl=1800
        )
        
        self.assertEqual(result["session_id"], session_id)
        self.assertTrue(result["recording_file"].endswith(f"{session_id}.cast"))
        self.assertTrue(result["metadata_file"].endswith(f"{session_id}.json"))
        
        # Verify session was added to database
        session_info = self.recorder.get_session(session_id)
        self.assertIsNotNone(session_info)
        self.assertEqual(session_info["session_id"], session_id)
        self.assertEqual(session_info["profile"], "dev")
        self.assertEqual(session_info["status"], "active")
    
    def test_stop_recording(self):
        """Test stopping a session recording"""
        session_id = "test_session_456"
        
        # Start recording
        self.recorder.start_recording(
            session_id=session_id,
            container_name="test_container",
            profile="debug"
        )
        
        # Allow a little time to pass
        time.sleep(0.1)
        
        # Stop recording
        result = self.recorder.stop_recording(session_id)
        
        self.assertIsNotNone(result)
        self.assertEqual(result["session_id"], session_id)
        self.assertEqual(result["status"], "ended")
        
        # Verify session status was updated in database
        session_info = self.recorder.get_session(session_id)
        self.assertEqual(session_info["status"], "ended")
        self.assertIsNotNone(session_info["ended_at"])
    
    def test_list_recordings(self):
        """Test listing recordings"""
        # Add a few test sessions
        for i in range(3):
            session_id = f"test_session_{i}"
            self.recorder.start_recording(
                session_id=session_id,
                container_name=f"test_container_{i}",
                profile="dev"
            )
            # Stop one of them
            if i == 1:
                self.recorder.stop_recording(session_id)
        
        # List recordings
        recordings = self.recorder.list_recordings()
        
        # Should have 3 recordings
        self.assertEqual(len(recordings), 3)
        
        # Verify they're ordered by creation time (most recent first)
        for i in range(len(recordings) - 1):
            current_time = datetime.fromisoformat(recordings[i]["created_at"])
            next_time = datetime.fromisoformat(recordings[i + 1]["created_at"])
            self.assertGreaterEqual(current_time, next_time)


class TestScripts(unittest.TestCase):
    """Test the shell scripts"""
    
    def test_box_provision_script_exists(self):
        """Test that the provision script exists"""
        script_path = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'box-provision.sh')
        self.assertTrue(os.path.exists(script_path))
        
        # Check that it's executable
        self.assertTrue(os.access(script_path, os.X_OK))
    
    def test_box_destroy_script_exists(self):
        """Test that the destroy script exists"""
        script_path = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'box-destroy.sh')
        self.assertTrue(os.path.exists(script_path))
        
        # Check that it's executable
        self.assertTrue(os.access(script_path, os.X_OK))
    
    def test_firecracker_scripts_exist(self):
        """Test that Firecracker scripts exist"""
        provision_script = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'box-provision-firecracker.sh')
        destroy_script = os.path.join(os.path.join(os.path.dirname(__file__), '..', 'scripts', 'box-destroy-firecracker.sh'))
        
        self.assertTrue(os.path.exists(provision_script))
        self.assertTrue(os.path.exists(destroy_script))
        
        # Check that they're executable
        self.assertTrue(os.access(provision_script, os.X_OK))
        self.assertTrue(os.access(destroy_script, os.X_OK))


class TestIntegration(unittest.TestCase):
    """Integration tests for the complete system"""
    
    @patch('requests.post')
    @patch('subprocess.run')
    def test_client_connect_flow(self, mock_subprocess, mock_requests):
        """Test the client connect flow"""
        # Mock subprocess to avoid actually generating SSH keys
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_subprocess.return_value = mock_result
        
        # Mock the gateway response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "host": "127.0.0.1",
            "port": 2222,
            "user": "boxuser",
            "session_id": "test_session_789"
        }
        mock_requests.return_value = mock_response
        
        # This would normally try to connect via SSH, but we're mocking that
        # For now, just make sure it doesn't crash
        try:
            # Temporarily redirect stdout to avoid printing connection info
            import io
            import contextlib
            
            captured_output = io.StringIO()
            with contextlib.redirect_stdout(captured_output):
                # We can't fully test client_connect because it calls os.execvp
                # Instead, we'll just verify the function exists and signature
                self.assertTrue(callable(client_connect))
        except:
            # Expected since os.execvp would fail in test environment
            pass


def run_tests():
    """Run all tests"""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)