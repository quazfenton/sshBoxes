#!/usr/bin/env python3
"""
Session recording module for sshBox
Records SSH sessions using the 'script' command and stores metadata
Includes security fixes for path traversal prevention
"""
import os
import json
import subprocess
import time
import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

from api.exceptions import PathTraversalError, InvalidInputError, RecordingError

logger = logging.getLogger("session_recorder")


def is_safe_path(base_dir: Path, target_path: Path) -> bool:
    """
    Validate that target_path is within base_dir to prevent path traversal
    
    Args:
        base_dir: Base directory that should contain the target
        target_path: Path to validate
    
    Returns:
        True if target_path is within base_dir, False otherwise
    
    Raises:
        PathTraversalError: If path traversal is detected
    """
    try:
        # Resolve to absolute paths, resolving any symlinks and .. components
        base_resolved = base_dir.resolve(strict=True)
        target_resolved = target_path.resolve()
        
        # Check if target is relative to base
        try:
            target_resolved.relative_to(base_resolved)
            return True
        except ValueError:
            # Path is not relative to base
            logger.warning(
                f"Path traversal attempt detected: {target_path} is not within {base_dir}"
            )
            return False
    except (OSError, RuntimeError) as e:
        # If we can't resolve the paths, be conservative and reject
        logger.error(f"Error validating path: {e}")
        return False


def validate_session_id(session_id: str) -> bool:
    """
    Validate session ID format to prevent injection attacks
    
    Args:
        session_id: Session ID to validate
    
    Returns:
        True if valid
    
    Raises:
        InvalidInputError: If session_id is invalid
    """
    if not session_id:
        raise InvalidInputError(field="session_id", reason="Session ID cannot be empty")
    
    if len(session_id) > 128:
        raise InvalidInputError(
            field="session_id",
            reason="Session ID too long (max 128 characters)",
            value=session_id
        )
    
    if not re.match(r'^[a-zA-Z0-9_-]+$', session_id):
        raise InvalidInputError(
            field="session_id",
            reason="Session ID must contain only alphanumeric characters, dashes, and underscores",
            value=session_id
        )
    
    return True


class SessionRecorder:
    """Secure session recorder with path traversal prevention"""
    
    def __init__(self, recordings_dir: str = "/tmp/sshbox_recordings"):
        """
        Initialize session recorder
        
        Args:
            recordings_dir: Directory to store recordings
        
        Raises:
            RecordingError: If recordings directory cannot be created
        """
        self.recordings_dir = Path(recordings_dir)
        
        try:
            self.recordings_dir.mkdir(parents=True, exist_ok=True)
            
            # Verify directory is writable
            if not os.access(self.recordings_dir, os.W_OK):
                raise RecordingError(
                    reason=f"Recordings directory not writable: {recordings_dir}",
                )
        except Exception as e:
            logger.error(f"Failed to create recordings directory: {e}")
            raise RecordingError(
                reason=f"Failed to create recordings directory: {e}"
            )
        
        logger.info(f"SessionRecorder initialized with directory: {recordings_dir}")

    def start_recording(
        self,
        session_id: str,
        user_id: str,
        profile: str,
        ttl: int,
        metadata_extra: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """
        Start recording a session
        
        Args:
            session_id: Unique session identifier
            user_id: User identifier
            profile: Session profile
            ttl: Time to live in seconds
            metadata_extra: Optional additional metadata
        
        Returns:
            Dictionary with recording file paths
        
        Raises:
            InvalidInputError: If session_id is invalid
            RecordingError: If recording cannot be started
        """
        # Validate session_id to prevent path traversal and injection
        validate_session_id(session_id)
        
        # Validate profile
        if not re.match(r'^[a-zA-Z0-9_-]+$', profile):
            raise InvalidInputError(
                field="profile",
                reason="Profile must contain only alphanumeric characters, dashes, and underscores",
                value=profile
            )
        
        try:
            recording_file = self.recordings_dir / f"{session_id}.typescript"
            timing_file = self.recordings_dir / f"{session_id}.timing"
            metadata_file = self.recordings_dir / f"{session_id}.json"
            
            # Verify paths are safe (defense in depth)
            for path in [recording_file, timing_file, metadata_file]:
                if not is_safe_path(self.recordings_dir, path):
                    raise PathTraversalError(
                        path=str(path),
                        base_dir=str(self.recordings_dir)
                    )

            # Create metadata
            metadata = {
                "session_id": session_id,
                "user_id": user_id,
                "profile": profile,
                "ttl": ttl,
                "start_time": datetime.now(timezone.utc).isoformat(),
                "recording_file": str(recording_file),
                "timing_file": str(timing_file),
                "status": "recording"
            }
            
            # Add extra metadata if provided
            if metadata_extra:
                metadata.update(metadata_extra)

            # Write metadata file
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            # Set restrictive permissions on metadata file
            os.chmod(metadata_file, 0o600)

            logger.info(
                f"Started recording for session {session_id}",
                extra={"session_id": session_id, "user_id": user_id}
            )

            return {
                "recording_file": str(recording_file),
                "timing_file": str(timing_file),
                "metadata_file": str(metadata_file),
                "session_id": session_id
            }
            
        except (InvalidInputError, PathTraversalError):
            raise
        except Exception as e:
            logger.error(f"Error starting recording for session {session_id}: {e}")
            raise RecordingError(
                reason=f"Failed to start recording: {e}",
                session_id=session_id
            )

    def stop_recording(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Stop recording and finalize session data
        
        Args:
            session_id: Session ID to stop
        
        Returns:
            Updated metadata or None if not found
        
        Raises:
            InvalidInputError: If session_id is invalid
        """
        # Validate session_id
        validate_session_id(session_id)
        
        metadata_file = self.recordings_dir / f"{session_id}.json"

        if not metadata_file.exists():
            logger.warning(f"Metadata file not found for session {session_id}")
            return None
        
        # Verify path is safe
        if not is_safe_path(self.recordings_dir, metadata_file):
            raise PathTraversalError(
                path=str(metadata_file),
                base_dir=str(self.recordings_dir)
            )

        try:
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)

            # Update metadata
            metadata["end_time"] = datetime.now(timezone.utc).isoformat()
            metadata["status"] = "completed"
            
            start_time = datetime.fromisoformat(metadata["start_time"])
            end_time = datetime.fromisoformat(metadata["end_time"])
            metadata["duration_seconds"] = (end_time - start_time).total_seconds()

            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            
            # Set restrictive permissions
            os.chmod(metadata_file, 0o600)

            logger.info(
                f"Stopped recording for session {session_id}, duration: {metadata['duration_seconds']:.1f}s",
                extra={"session_id": session_id}
            )

            return metadata
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid metadata JSON for session {session_id}: {e}")
            raise RecordingError(
                reason="Invalid metadata format",
                session_id=session_id
            )
        except Exception as e:
            logger.error(f"Error stopping recording for session {session_id}: {e}")
            raise RecordingError(
                reason=f"Failed to stop recording: {e}",
                session_id=session_id
            )

    def get_recording(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve recording metadata and content
        
        Args:
            session_id: Session ID to retrieve
        
        Returns:
            Recording metadata with content or None if not found
        
        Raises:
            InvalidInputError: If session_id is invalid
            PathTraversalError: If path traversal is detected
        """
        # Validate session_id
        validate_session_id(session_id)
        
        metadata_file = self.recordings_dir / f"{session_id}.json"

        if not metadata_file.exists():
            return None
        
        # Verify metadata file path is safe
        if not is_safe_path(self.recordings_dir, metadata_file):
            raise PathTraversalError(
                path=str(metadata_file),
                base_dir=str(self.recordings_dir)
            )

        try:
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)

            # Validate and read recording file
            recording_file = Path(metadata.get("recording_file", ""))
            
            if not recording_file:
                logger.warning(f"No recording file specified for session {session_id}")
                return metadata
            
            # CRITICAL: Verify recording file is within recordings directory
            if not is_safe_path(self.recordings_dir, recording_file):
                logger.error(
                    f"Path traversal attempt in recording file: {recording_file}"
                )
                raise PathTraversalError(
                    path=str(recording_file),
                    base_dir=str(self.recordings_dir)
                )

            if recording_file.exists():
                # Verify file size before reading
                max_size = 100 * 1024 * 1024  # 100MB
                if recording_file.stat().st_size > max_size:
                    logger.warning(
                        f"Recording file too large: {recording_file.stat().st_size} bytes"
                    )
                    metadata["content_truncated"] = True
                    metadata["content"] = "[File too large to display]"
                else:
                    with open(recording_file, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    metadata["content"] = content
            else:
                logger.warning(f"Recording file not found: {recording_file}")
                metadata["content"] = None

            return metadata
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid metadata JSON for session {session_id}: {e}")
            raise RecordingError(
                reason="Invalid metadata format",
                session_id=session_id
            )
        except (PathTraversalError, InvalidInputError):
            raise
        except Exception as e:
            logger.error(f"Error retrieving recording for session {session_id}: {e}")
            raise RecordingError(
                reason=f"Failed to retrieve recording: {e}",
                session_id=session_id
            )

    def list_recordings(
        self,
        limit: int = 100,
        user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List all available recordings
        
        Args:
            limit: Maximum number of recordings to return
            user_id: Optional filter by user ID
        
        Returns:
            List of recording metadata
        """
        recordings = []
        
        try:
            # Get metadata files sorted by modification time
            meta_files = sorted(
                self.recordings_dir.glob("*.json"),
                key=lambda f: f.stat().st_mtime,
                reverse=True
            )
            
            count = 0
            for meta_file in meta_files:
                if count >= limit:
                    break
                
                # Verify path is safe
                if not is_safe_path(self.recordings_dir, meta_file):
                    logger.warning(f"Skipping unsafe metadata file: {meta_file}")
                    continue
                
                try:
                    with open(meta_file, 'r') as f:
                        metadata = json.load(f)
                    
                    # Filter by user_id if specified
                    if user_id and metadata.get("user_id") != user_id:
                        continue
                    
                    # Remove content from list view
                    metadata.pop("content", None)
                    recordings.append(metadata)
                    count += 1
                    
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"Error reading metadata file {meta_file}: {e}")
                    continue
            
            return recordings
            
        except Exception as e:
            logger.error(f"Error listing recordings: {e}")
            raise RecordingError(reason=f"Failed to list recordings: {e}")

    def delete_recording(self, session_id: str) -> bool:
        """
        Delete a recording and its metadata
        
        Args:
            session_id: Session ID to delete
        
        Returns:
            True if deleted, False if not found
        
        Raises:
            InvalidInputError: If session_id is invalid
        """
        # Validate session_id
        validate_session_id(session_id)
        
        files_to_delete = [
            self.recordings_dir / f"{session_id}.json",
            self.recordings_dir / f"{session_id}.typescript",
            self.recordings_dir / f"{session_id}.timing"
        ]
        
        deleted = False
        for file_path in files_to_delete:
            # Verify path is safe before deleting
            if not is_safe_path(self.recordings_dir, file_path):
                logger.error(f"Path traversal attempt in delete: {file_path}")
                continue
            
            if file_path.exists():
                try:
                    file_path.unlink()
                    deleted = True
                    logger.info(f"Deleted recording file: {file_path}")
                except OSError as e:
                    logger.error(f"Failed to delete {file_path}: {e}")
        
        return deleted

    def cleanup_old_recordings(self, max_age_days: int = 7) -> int:
        """
        Remove recordings older than specified age
        
        Args:
            max_age_days: Maximum age in days
        
        Returns:
            Number of recordings deleted
        """
        deleted_count = 0
        cutoff_time = datetime.now(timezone.utc).timestamp() - (max_age_days * 24 * 60 * 60)
        
        try:
            for meta_file in self.recordings_dir.glob("*.json"):
                # Verify path is safe
                if not is_safe_path(self.recordings_dir, meta_file):
                    continue
                
                try:
                    # Check file modification time
                    if meta_file.stat().st_mtime < cutoff_time:
                        session_id = meta_file.stem
                        
                        # Delete all related files
                        if self.delete_recording(session_id):
                            deleted_count += 1
                            logger.info(f"Cleaned up old recording: {session_id}")
                            
                except (OSError, json.JSONDecodeError) as e:
                    logger.warning(f"Error processing {meta_file}: {e}")
                    continue
            
            logger.info(f"Cleaned up {deleted_count} old recordings")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            raise RecordingError(reason=f"Failed to cleanup old recordings: {e}")

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