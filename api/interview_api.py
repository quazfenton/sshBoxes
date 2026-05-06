#!/usr/bin/env python3
"""
Interview Mode API Endpoints for sshBox
RESTful API for scheduling and managing technical interviews
"""
import os
import sys
from datetime import datetime
from typing import List, Optional
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from fastapi.middleware.cors import CORSMiddleware

from api.logging_config import setup_logging
from api.exceptions import SessionNotFoundError, InvalidInputError
from api.interview_mode import (
    get_interview_manager,
    InterviewSession,
    InterviewProblem,
    InterviewLanguage
)
from api.quota_manager import get_quota_manager

logger = setup_logging("interview_api")

app = FastAPI(
    title="sshBox Interview API",
    description="API for scheduling and managing technical interviews",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Request/Response Models
# ============================================================================

class ScheduleInterviewRequest(BaseModel):
    """Request to schedule an interview"""
    candidate_email: str = Field(..., description="Candidate's email address")
    candidate_name: Optional[str] = Field(None, description="Candidate's name")
    interviewer_email: str = Field(..., description="Interviewer's email")
    problem_id: str = Field(default="two_sum", description="Problem ID to use")
    language: str = Field(default="python", description="Programming language")
    scheduled_at: Optional[str] = Field(None, description="Scheduled time (ISO format)")
    ttl_seconds: int = Field(default=3600, ge=1800, le=7200, description="Session TTL")


class InterviewResponse(BaseModel):
    """Interview session response"""
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
    session_id: Optional[str]
    observer_token: str
    recording_url: Optional[str]
    score: Optional[int]
    feedback: Optional[str]
    observer_link: Optional[str] = None


class StartInterviewResponse(BaseModel):
    """Response when starting an interview"""
    interview_id: str
    session_id: str
    host: str
    port: int
    user: str
    problem: dict
    observer_link: str
    expires_at: str


class CompleteInterviewRequest(BaseModel):
    """Request to complete an interview"""
    score: Optional[int] = Field(None, ge=0, le=100, description="Score 0-100")
    feedback: Optional[str] = Field(None, description="Interviewer feedback")


class ProblemResponse(BaseModel):
    """Interview problem response"""
    id: str
    title: str
    description: str
    difficulty: str
    language: str
    starter_code: str


# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/", summary="Root endpoint")
async def root():
    """API information"""
    return {
        "service": "sshBox Interview API",
        "version": "1.0.0",
        "endpoints": {
            "schedule": "POST /interviews/schedule - Schedule new interview",
            "start": "POST /interviews/{id}/start - Start interview session",
            "complete": "POST /interviews/{id}/complete - Complete interview",
            "cancel": "POST /interviews/{id}/cancel - Cancel interview",
            "observer": "GET /interviews/{id}/observer - Get observer view",
            "list": "GET /interviews - List interviews",
            "problems": "GET /problems - List available problems"
        }
    }


@app.get("/health", summary="Health check")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.post("/interviews/schedule", summary="Schedule new interview", response_model=InterviewResponse)
async def schedule_interview(request: ScheduleInterviewRequest):
    """
    Schedule a new technical interview session
    
    - **candidate_email**: Candidate's email address
    - **interviewer_email**: Interviewer's email
    - **problem_id**: Coding problem to use (default: two_sum)
    - **language**: Programming language (default: python)
    - **ttl_seconds**: Session duration in seconds (default: 3600)
    """
    try:
        interview_mgr = get_interview_manager()
        
        # Schedule interview
        interview = interview_mgr.schedule_interview(
            candidate_email=request.candidate_email,
            interviewer_id=request.interviewer_email,  # Using email as ID for simplicity
            interviewer_email=request.interviewer_email,
            problem_id=request.problem_id,
            language=request.language,
            ttl_seconds=request.ttl_seconds
        )
        
        # Generate observer link
        observer_link = f"/web/?session={interview.id}&observer=true&token={interview.observer_token}"
        
        response = InterviewResponse(
            **interview.__dict__,
            observer_link=observer_link
        )
        
        logger.info(f"Scheduled interview {interview.id} for {request.candidate_email}")
        return response
        
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=e.to_dict())
    except Exception as e:
        logger.error(f"Error scheduling interview: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/interviews/{interview_id}/start", summary="Start interview session", response_model=StartInterviewResponse)
async def start_interview(interview_id: str, background_tasks: BackgroundTasks):
    """
    Start an interview session - creates the sshBox and returns connection info
    
    - **interview_id**: The interview ID to start
    """
    try:
        interview_mgr = get_interview_manager()
        
        # Start interview session
        connection_info = interview_mgr.start_interview_session(
            interview_id=interview_id
        )
        
        # Calculate expiration
        ttl = connection_info.get('ttl', 3600)
        expires_at = datetime.utcnow().isoformat()
        
        response = StartInterviewResponse(
            interview_id=interview_id,
            session_id=connection_info['session_id'],
            host=connection_info['host'],
            port=connection_info['port'],
            user=connection_info['user'],
            problem=connection_info.get('problem', {}),
            observer_link=connection_info.get('observer_link', ''),
            expires_at=expires_at
        )
        
        logger.info(f"Started interview session {interview_id}")
        return response
        
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.to_dict())
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=e.to_dict())
    except Exception as e:
        logger.error(f"Error starting interview: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/interviews/{interview_id}/complete", summary="Complete interview", response_model=InterviewResponse)
async def complete_interview(interview_id: str, request: CompleteInterviewRequest):
    """
    Complete an interview session with optional score and feedback
    
    - **interview_id**: The interview ID to complete
    - **score**: Optional score 0-100
    - **feedback**: Optional interviewer feedback
    """
    try:
        interview_mgr = get_interview_manager()
        
        # Complete interview
        interview = interview_mgr.complete_interview(
            interview_id=interview_id,
            score=request.score,
            feedback=request.feedback
        )
        
        response = InterviewResponse(**interview.__dict__)
        
        logger.info(f"Completed interview {interview_id} with score {request.score}")
        return response
        
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.to_dict())
    except Exception as e:
        logger.error(f"Error completing interview: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/interviews/{interview_id}/cancel", summary="Cancel interview", response_model=InterviewResponse)
async def cancel_interview(interview_id: str, reason: Optional[str] = None):
    """
    Cancel a scheduled interview
    
    - **interview_id**: The interview ID to cancel
    - **reason**: Optional cancellation reason
    """
    try:
        interview_mgr = get_interview_manager()
        
        # Cancel interview
        interview = interview_mgr.cancel_interview(
            interview_id=interview_id,
            reason=reason
        )
        
        response = InterviewResponse(**interview.__dict__)
        
        logger.info(f"Cancelled interview {interview_id}: {reason}")
        return response
        
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.to_dict())
    except Exception as e:
        logger.error(f"Error cancelling interview: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/interviews/{interview_id}/observer", summary="Get observer view")
async def get_observer_view(interview_id: str):
    """
    Get observer view data for an interview
    
    Returns problem details, session status, and observer link
    """
    try:
        interview_mgr = get_interview_manager()
        
        # Get observer view
        observer_data = interview_mgr.get_observer_view(interview_id)
        
        return observer_data
        
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=e.to_dict())
    except Exception as e:
        logger.error(f"Error getting observer view: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/interviews", summary="List interviews")
async def list_interviews(
    interviewer_id: Optional[str] = None,
    candidate_email: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50
):
    """
    List interviews with optional filters
    
    - **interviewer_id**: Filter by interviewer
    - **candidate_email**: Filter by candidate
    - **status**: Filter by status (scheduled, in_progress, completed, cancelled)
    - **limit**: Maximum results (default: 50)
    """
    try:
        interview_mgr = get_interview_manager()
        
        # List interviews
        interviews = interview_mgr.list_interviews(
            interviewer_id=interviewer_id,
            candidate_email=candidate_email,
            status=status,
            limit=limit
        )
        
        return {
            "interviews": [InterviewResponse(**i.__dict__) for i in interviews],
            "count": len(interviews)
        }
        
    except Exception as e:
        logger.error(f"Error listing interviews: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/problems", summary="List available problems", response_model=List[ProblemResponse])
async def list_problems():
    """
    List all available interview problems
    """
    try:
        interview_mgr = get_interview_manager()
        
        # Get problems
        problems = list(interview_mgr.problems.values())
        
        return [
            ProblemResponse(
                id=p.id,
                title=p.title,
                description=p.description,
                difficulty=p.difficulty,
                language=p.language,
                starter_code=p.starter_code
            )
            for p in problems
        ]
        
    except Exception as e:
        logger.error(f"Error listing problems: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/problems/{problem_id}", summary="Get problem details")
async def get_problem(problem_id: str):
    """
    Get details for a specific interview problem
    """
    try:
        interview_mgr = get_interview_manager()
        
        if problem_id not in interview_mgr.problems:
            raise HTTPException(status_code=404, detail=f"Problem {problem_id} not found")
        
        problem = interview_mgr.problems[problem_id]
        
        return ProblemResponse(
            id=problem.id,
            title=problem.title,
            description=problem.description,
            difficulty=problem.difficulty,
            language=problem.language,
            starter_code=problem.starter_code
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting problem: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get('INTERVIEW_API_PORT', 8083)),
        log_level="info"
    )
