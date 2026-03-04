#!/usr/bin/env python3
"""
Interview Mode for sshBox
Purpose-built for technical interviews with observer mode, recording, and scoring
"""
import os
import sys
import json
import uuid
import logging
import tempfile
import subprocess
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from pathlib import Path
from dataclasses import dataclass, asdict
from enum import Enum
import asyncio
import requests

from api.exceptions import InvalidInputError, SessionNotFoundError
from api.logging_config import setup_logging
from api.session_recorder import SessionRecorder
from api.quota_manager import get_quota_manager

logger = setup_logging("interview_mode")


class InterviewStatus(str, Enum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class InterviewLanguage(str, Enum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    GO = "go"
    RUST = "rust"
    JAVA = "java"
    CPP = "cpp"
    CSHARP = "csharp"


@dataclass
class InterviewProblem:
    """Coding problem for interview"""
    id: str
    title: str
    description: str
    difficulty: str  # easy, medium, hard
    language: str
    starter_code: str
    test_cases: List[Dict]
    expected_output: List[Any]
    time_limit_seconds: int = 300
    memory_limit_mb: int = 256


@dataclass
class InterviewSession:
    """Interview session data"""
    id: str
    candidate_email: str
    candidate_name: Optional[str]
    interviewer_id: str
    interviewer_email: str
    problem_id: str
    language: str
    status: str
    scheduled_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    session_id: Optional[str]  # Linked sshBox session
    observer_token: str
    recording_url: Optional[str]
    score: Optional[int]
    feedback: Optional[str]
    created_at: str
    updated_at: str


class InterviewManager:
    """
    Manages technical interview sessions
    
    Usage:
        manager = InterviewManager()
        
        # Schedule interview
        interview = manager.schedule_interview(
            candidate_email="candidate@example.com",
            interviewer_id="int_123",
            problem_id="two_sum",
            language="python"
        )
        
        # Start interview session
        session = manager.start_interview_session(interview.id)
        
        # Get observer view
        observer_data = manager.get_observer_view(interview.id)
    """
    
    # Default interview problems
    DEFAULT_PROBLEMS = {
        "two_sum": InterviewProblem(
            id="two_sum",
            title="Two Sum",
            description="Given an array of integers nums and an integer target, return indices of the two numbers such that they add up to target.",
            difficulty="easy",
            language="python",
            starter_code="""def two_sum(nums, target):
    \"\"\"
    Args:
        nums: List[int]
        target: int
    Returns:
        List[int]
    \"\"\"
    # Write your code here
    
""",
            test_cases=[
                {"input": [[2, 7, 11, 15], 9], "expected": [0, 1]},
                {"input": [[3, 2, 4], 6], "expected": [1, 2]},
                {"input": [[3, 3], 6], "expected": [0, 1]},
            ],
            expected_output=[[0, 1], [1, 2], [0, 1]]
        ),
        "valid_parentheses": InterviewProblem(
            id="valid_parentheses",
            title="Valid Parentheses",
            description="Given a string s containing just the characters '(', ')', '{', '}', '[' and ']', determine if the input string is valid.",
            difficulty="easy",
            language="python",
            starter_code="""def is_valid(s):
    \"\"\"
    Args:
        s: str
    Returns:
        bool
    \"\"\"
    # Write your code here
    
""",
            test_cases=[
                {"input": ["()"], "expected": True},
                {"input": ["()[]{}"], "expected": True},
                {"input": ["(]"], "expected": False},
            ],
            expected_output=[True, True, False]
        ),
        "merge_intervals": InterviewProblem(
            id="merge_intervals",
            title="Merge Intervals",
            description="Given an array of intervals where intervals[i] = [start, end], merge all overlapping intervals.",
            difficulty="medium",
            language="python",
            starter_code="""def merge(intervals):
    \"\"\"
    Args:
        intervals: List[List[int]]
    Returns:
        List[List[int]]
    \"\"\"
    # Write your code here
    
""",
            test_cases=[
                {"input": [[[1, 3], [2, 6], [8, 10], [15, 18]]], "expected": [[1, 6], [8, 10], [15, 18]]},
                {"input": [[[1, 4], [4, 5]]], "expected": [[1, 5]]},
            ],
            expected_output=[[[1, 6], [8, 10], [15, 18]], [[1, 5]]]
        ),
    }
    
    def __init__(
        self,
        storage_dir: str = "/var/lib/sshbox/interviews",
        gateway_url: str = "http://localhost:8080"
    ):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        
        self.gateway_url = gateway_url
        self.recorder = SessionRecorder()
        self.quota_mgr = get_quota_manager()
        
        # Load problems
        self.problems = self._load_problems()
        
        # Interview sessions cache
        self._sessions: Dict[str, InterviewSession] = {}
        
        logger.info(f"InterviewManager initialized at {storage_dir}")
    
    def _load_problems(self) -> Dict[str, InterviewProblem]:
        """Load interview problems"""
        problems = dict(self.DEFAULT_PROBLEMS)
        
        # Load custom problems from storage
        problems_dir = self.storage_dir / "problems"
        problems_dir.mkdir(parents=True, exist_ok=True)
        
        for problem_file in problems_dir.glob("*.json"):
            try:
                with open(problem_file, 'r') as f:
                    data = json.load(f)
                    problem = InterviewProblem(**data)
                    problems[problem.id] = problem
            except Exception as e:
                logger.warning(f"Failed to load problem {problem_file}: {e}")
        
        return problems
    
    def schedule_interview(
        self,
        candidate_email: str,
        interviewer_id: str,
        interviewer_email: str,
        problem_id: str = "two_sum",
        language: str = "python",
        scheduled_at: datetime = None,
        ttl_seconds: int = 3600
    ) -> InterviewSession:
        """
        Schedule a new interview session
        
        Args:
            candidate_email: Candidate's email
            interviewer_id: Interviewer's user ID
            interviewer_email: Interviewer's email
            problem_id: Problem to use
            language: Programming language
            scheduled_at: When to schedule (default: now)
            ttl_seconds: Session TTL
        
        Returns:
            InterviewSession with observer token
        """
        # Validate problem
        if problem_id not in self.problems:
            raise InvalidInputError(
                field="problem_id",
                reason=f"Unknown problem: {problem_id}",
                value=problem_id
            )
        
        # Validate language
        if language not in [l.value for l in InterviewLanguage]:
            raise InvalidInputError(
                field="language",
                reason=f"Unsupported language: {language}",
                value=language
            )
        
        # Generate IDs
        interview_id = f"int_{uuid.uuid4().hex[:12]}"
        observer_token = uuid.uuid4().hex
        
        now = scheduled_at or datetime.utcnow()
        
        interview = InterviewSession(
            id=interview_id,
            candidate_email=candidate_email,
            candidate_name=None,
            interviewer_id=interviewer_id,
            interviewer_email=interviewer_email,
            problem_id=problem_id,
            language=language,
            status=InterviewStatus.SCHEDULED.value,
            scheduled_at=now.isoformat(),
            started_at=None,
            completed_at=None,
            session_id=None,
            observer_token=observer_token,
            recording_url=None,
            score=None,
            feedback=None,
            created_at=datetime.utcnow().isoformat(),
            updated_at=datetime.utcnow().isoformat()
        )
        
        # Save interview
        self._save_interview(interview)
        self._sessions[interview_id] = interview
        
        logger.info(f"Scheduled interview {interview_id} for {candidate_email}")
        return interview
    
    def start_interview_session(
        self,
        interview_id: str,
        candidate_user_id: str = None
    ) -> Dict[str, Any]:
        """
        Start the sshBox session for an interview
        
        Args:
            interview_id: Interview ID
            candidate_user_id: Candidate's user ID for quota
        
        Returns:
            Connection info for the interview box
        """
        interview = self._get_interview(interview_id)
        
        if not interview:
            raise SessionNotFoundError(interview_id)
        
        if interview.status != InterviewStatus.SCHEDULED.value:
            raise InvalidInputError(
                field="status",
                reason=f"Interview cannot be started in status: {interview.status}"
            )
        
        # Get problem starter code
        problem = self.problems.get(interview.problem_id)
        
        # Create sshBox session with interview profile
        import requests
        
        # Generate temporary key for candidate
        import subprocess
        import tempfile
        
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / "interview_key"
            subprocess.run(
                ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", ""],
                capture_output=True,
                check=True
            )
            
            with open(f"{key_path}.pub", 'r') as f:
                pubkey = f.read().strip()
            
            # Request session from gateway
            response = requests.post(
                f"{self.gateway_url}/request",
                json={
                    "token": interview.observer_token,  # Use observer token as auth
                    "pubkey": pubkey,
                    "profile": "interview",
                    "ttl": 3600
                },
                timeout=30
            )
            
            if response.status_code != 200:
                raise Exception(f"Gateway error: {response.text}")
            
            connection_info = response.json()
        
        # Update interview
        interview.status = InterviewStatus.IN_PROGRESS.value
        interview.started_at = datetime.utcnow().isoformat()
        interview.session_id = connection_info['session_id']
        interview.updated_at = datetime.utcnow().isoformat()
        
        self._save_interview(interview)
        
        # Start recording
        try:
            recording_info = self.recorder.start_recording(
                session_id=interview.session_id,
                user_id=interview.candidate_email,
                profile="interview",
                ttl=3600,
                metadata_extra={
                    "interview_id": interview.id,
                    "problem_id": interview.problem_id,
                    "language": interview.language,
                    "is_interview": True
                }
            )
            interview.recording_url = recording_info.get('recording_file')
        except Exception as e:
            logger.warning(f"Failed to start recording: {e}")
        
        self._save_interview(interview)
        
        # Return connection info with interview context
        return {
            **connection_info,
            "interview_id": interview.id,
            "problem": asdict(problem) if problem else None,
            "observer_link": f"/web/?session={connection_info['session_id']}&observer=true"
        }
    
    def get_observer_view(self, interview_id: str) -> Dict[str, Any]:
        """
        Get observer view data for an interview
        
        Args:
            interview_id: Interview ID
        
        Returns:
            Observer view data
        """
        interview = self._get_interview(interview_id)
        
        if not interview:
            raise SessionNotFoundError(interview_id)
        
        problem = self.problems.get(interview.problem_id)
        
        return {
            "interview_id": interview.id,
            "candidate_email": interview.candidate_email,
            "interviewer_email": interview.interviewer_email,
            "status": interview.status,
            "problem": asdict(problem) if problem else None,
            "language": interview.language,
            "started_at": interview.started_at,
            "session_active": interview.session_id is not None and interview.status == "in_progress",
            "recording_url": interview.recording_url,
            "observer_token": interview.observer_token
        }
    
    def complete_interview(
        self,
        interview_id: str,
        score: int = None,
        feedback: str = None
    ) -> InterviewSession:
        """
        Complete an interview
        
        Args:
            interview_id: Interview ID
            score: Optional score (0-100)
            feedback: Optional feedback text
        
        Returns:
            Updated InterviewSession
        """
        interview = self._get_interview(interview_id)
        
        if not interview:
            raise SessionNotFoundError(interview_id)
        
        interview.status = InterviewStatus.COMPLETED.value
        interview.completed_at = datetime.utcnow().isoformat()
        interview.score = score
        interview.feedback = feedback
        interview.updated_at = datetime.utcnow().isoformat()
        
        self._save_interview(interview)
        
        # Stop recording
        try:
            self.recorder.stop_recording(interview.session_id)
        except Exception as e:
            logger.warning(f"Failed to stop recording: {e}")
        
        logger.info(f"Completed interview {interview_id} with score {score}")
        return interview
    
    def cancel_interview(self, interview_id: str, reason: str = None) -> InterviewSession:
        """Cancel an interview"""
        interview = self._get_interview(interview_id)
        
        if not interview:
            raise SessionNotFoundError(interview_id)
        
        interview.status = InterviewStatus.CANCELLED.value
        interview.updated_at = datetime.utcnow().isoformat()
        
        self._save_interview(interview)
        
        logger.info(f"Cancelled interview {interview_id}: {reason}")
        return interview
    
    def list_interviews(
        self,
        interviewer_id: str = None,
        candidate_email: str = None,
        status: str = None,
        limit: int = 50
    ) -> List[InterviewSession]:
        """List interviews with filters"""
        interviews = list(self._sessions.values())
        
        if interviewer_id:
            interviews = [i for i in interviews if i.interviewer_id == interviewer_id]
        
        if candidate_email:
            interviews = [i for i in interviews if i.candidate_email == candidate_email]
        
        if status:
            interviews = [i for i in interviews if i.status == status]
        
        # Sort by scheduled_at descending
        interviews.sort(key=lambda x: x.scheduled_at, reverse=True)
        
        return interviews[:limit]
    
    def add_custom_problem(self, problem: InterviewProblem) -> bool:
        """Add a custom interview problem"""
        problem_file = self.storage_dir / "problems" / f"{problem.id}.json"
        
        with open(problem_file, 'w') as f:
            json.dump(asdict(problem), f, indent=2)
        
        self.problems[problem.id] = problem
        logger.info(f"Added custom problem: {problem.id}")
        return True
    
    def _get_interview(self, interview_id: str) -> Optional[InterviewSession]:
        """Get interview by ID"""
        # Check cache first
        if interview_id in self._sessions:
            return self._sessions[interview_id]
        
        # Load from storage
        interview_file = self.storage_dir / "interviews" / f"{interview_id}.json"
        
        if interview_file.exists():
            with open(interview_file, 'r') as f:
                data = json.load(f)
                return InterviewSession(**data)
        
        return None
    
    def _save_interview(self, interview: InterviewSession):
        """Save interview to storage"""
        interview_file = self.storage_dir / "interviews" / f"{interview.id}.json"
        interview_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(interview_file, 'w') as f:
            json.dump(asdict(interview), f, indent=2)


# Global interview manager instance
_interview_manager: Optional[InterviewManager] = None


def get_interview_manager() -> InterviewManager:
    """Get or create global interview manager"""
    global _interview_manager
    if _interview_manager is None:
        storage_dir = os.environ.get('INTERVIEW_STORAGE', '/var/lib/sshbox/interviews')
        gateway_url = os.environ.get('GATEWAY_URL', 'http://localhost:8080')
        _interview_manager = InterviewManager(
            storage_dir=storage_dir,
            gateway_url=gateway_url
        )
    return _interview_manager
