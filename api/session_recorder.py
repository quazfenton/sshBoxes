#!/usr/bin/env python3
"""
Session recording module for sshBox
Records SSH sessions using the 'script' command and stores metadata
"""
import os
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

class SessionRecorder:
    def __init__(self, recordings_dir="/tmp/sshbox_recordings"):
        self.recordings_dir = Path(recordings_dir)
        self.recordings_dir.mkdir(exist_ok=True)
    
    def start_recording(self, session_id, user_id, profile, ttl):
        """Start recording a session using the 'script' command"""
        recording_file = self.recordings_dir / f"{session_id}.typescript"
        timing_file = self.recordings_dir / f"{session_id}.timing"
        
        # Create metadata file
        metadata = {
            "session_id": session_id,
            "user_id": user_id,
            "profile": profile,
            "ttl": ttl,
            "start_time": datetime.utcnow().isoformat(),
            "recording_file": str(recording_file),
            "timing_file": str(timing_file)
        }
        
        metadata_file = self.recordings_dir / f"{session_id}.json"
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        # Note: The actual recording would happen by wrapping the SSH session
        # with the 'script' command, which is outside the scope of this module
        # but the files are prepared for it
        return {
            "recording_file": str(recording_file),
            "timing_file": str(timing_file),
            "metadata_file": str(metadata_file)
        }
    
    def stop_recording(self, session_id):
        """Stop recording and finalize session data"""
        metadata_file = self.recordings_dir / f"{session_id}.json"
        
        if metadata_file.exists():
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
            
            metadata["end_time"] = datetime.utcnow().isoformat()
            metadata["duration"] = (
                datetime.fromisoformat(metadata["end_time"]) - 
                datetime.fromisoformat(metadata["start_time"])
            ).total_seconds()
            
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            return metadata
        
        return None
    
    def get_recording(self, session_id):
        """Retrieve recording metadata and content"""
        metadata_file = self.recordings_dir / f"{session_id}.json"
        
        if not metadata_file.exists():
            return None
        
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
        
        recording_file = Path(metadata["recording_file"])
        if recording_file.exists():
            with open(recording_file, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            metadata["content"] = content
        
        return metadata
    
    def list_recordings(self):
        """List all available recordings"""
        recordings = []
        for meta_file in self.recordings_dir.glob("*.json"):
            with open(meta_file, 'r') as f:
                metadata = json.load(f)
                recordings.append(metadata)
        return recordings

# Example usage
if __name__ == "__main__":
    recorder = SessionRecorder()
    
    # Example: Start a recording
    session_info = recorder.start_recording(
        session_id="test_session_123",
        user_id="test_user",
        profile="dev",
        ttl=1800
    )
    
    print(f"Started recording: {session_info}")
    
    # Simulate some activity
    time.sleep(1)
    
    # Stop recording
    result = recorder.stop_recording("test_session_123")
    print(f"Stopped recording: {result}")
    
    # List all recordings
    all_recordings = recorder.list_recordings()
    print(f"All recordings: {len(all_recordings)}")