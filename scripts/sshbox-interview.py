#!/usr/bin/env python3
"""
sshBox Interview CLI
Command-line interface for scheduling and managing technical interviews

Usage:
    # Schedule interview
    ./sshbox-interview.py schedule \
        --candidate candidate@example.com \
        --interviewer interviewer@company.com \
        --problem two_sum \
        --language python

    # Start interview session
    ./sshbox-interview.py start --interview-id int_abc123

    # Complete interview
    ./sshbox-interview.py complete --interview-id int_abc123 --score 85 --feedback "Great problem solving!"

    # List interviews
    ./sshbox-interview.py list --status scheduled

    # Get observer link
    ./sshbox-interview.py observer --interview-id int_abc123
"""
import os
import sys
import json
import argparse
import webbrowser
from datetime import datetime
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from api.interview_mode import get_interview_manager, InterviewProblem


# API base URL
API_BASE = os.environ.get('INTERVIEW_API_URL', 'http://localhost:8083')


def schedule_interview(args):
    """Schedule a new interview"""
    try:
        interview_mgr = get_interview_manager()
        
        interview = interview_mgr.schedule_interview(
            candidate_email=args.candidate,
            interviewer_id=args.interviewer,
            interviewer_email=args.interviewer,
            problem_id=args.problem or 'two_sum',
            language=args.language or 'python',
            ttl_seconds=args.ttl or 3600
        )
        
        # Print results
        print(f"\n{'='*60}")
        print(f"📦 Interview Scheduled")
        print(f"{'='*60}")
        print(f"Interview ID:   {interview.id}")
        print(f"Candidate:      {interview.candidate_email}")
        print(f"Interviewer:    {interview.interviewer_email}")
        print(f"Problem:        {interview.problem_id}")
        print(f"Language:       {interview.language}")
        print(f"Status:         {interview.status}")
        print(f"Scheduled:      {interview.scheduled_at}")
        print(f"\nObserver Link:  /interviews/{interview.id}/observer")
        print(f"Observer Token: {interview.observer_token}")
        print(f"{'='*60}\n")
        
        # Optionally open observer link in browser
        if args.open:
            observer_url = f"{API_BASE}/interviews/{interview.id}/observer"
            print(f"Opening observer view in browser...")
            webbrowser.open(observer_url)
        
        return interview
        
    except Exception as e:
        print(f"❌ Error scheduling interview: {e}", file=sys.stderr)
        sys.exit(1)


def start_interview(args):
    """Start an interview session"""
    try:
        interview_mgr = get_interview_manager()
        
        print(f"Starting interview session {args.interview_id}...")
        
        connection = interview_mgr.start_interview_session(
            interview_id=args.interview_id
        )
        
        # Print results
        print(f"\n{'='*60}")
        print(f"🚀 Interview Session Started")
        print(f"{'='*60}")
        print(f"Interview ID:   {args.interview_id}")
        print(f"Session ID:     {connection['session_id']}")
        print(f"SSH Host:       {connection['host']}")
        print(f"SSH Port:       {connection['port']}")
        print(f"SSH User:       {connection['user']}")
        print(f"\nProblem:        {connection.get('problem', {}).get('title', 'N/A')}")
        print(f"Observer Link:  {connection.get('observer_link', 'N/A')}")
        print(f"{'='*60}\n")
        
        # Optionally open web terminal
        if args.open:
            web_url = connection.get('observer_link', '')
            if web_url:
                full_url = f"{API_BASE.replace('/interviews', '/web')}{web_url}"
                print(f"Opening web terminal in browser...")
                webbrowser.open(full_url)
        
        return connection
        
    except Exception as e:
        print(f"❌ Error starting interview: {e}", file=sys.stderr)
        sys.exit(1)


def complete_interview(args):
    """Complete an interview"""
    try:
        interview_mgr = get_interview_manager()
        
        interview = interview_mgr.complete_interview(
            interview_id=args.interview_id,
            score=args.score,
            feedback=args.feedback
        )
        
        # Print results
        print(f"\n{'='*60}")
        print(f"✅ Interview Completed")
        print(f"{'='*60}")
        print(f"Interview ID:   {interview.id}")
        print(f"Status:         {interview.status}")
        print(f"Completed:      {interview.completed_at}")
        if interview.score is not None:
            print(f"Score:          {interview.score}/100")
        if interview.feedback:
            print(f"Feedback:       {interview.feedback[:100]}...")
        print(f"{'='*60}\n")
        
        return interview
        
    except Exception as e:
        print(f"❌ Error completing interview: {e}", file=sys.stderr)
        sys.exit(1)


def cancel_interview(args):
    """Cancel an interview"""
    try:
        interview_mgr = get_interview_manager()
        
        interview = interview_mgr.cancel_interview(
            interview_id=args.interview_id,
            reason=args.reason
        )
        
        print(f"\n{'='*60}")
        print(f"🚫 Interview Cancelled")
        print(f"{'='*60}")
        print(f"Interview ID:   {interview.id}")
        print(f"Status:         {interview.status}")
        if args.reason:
            print(f"Reason:         {args.reason}")
        print(f"{'='*60}\n")
        
        return interview
        
    except Exception as e:
        print(f"❌ Error cancelling interview: {e}", file=sys.stderr)
        sys.exit(1)


def list_interviews(args):
    """List interviews"""
    try:
        interview_mgr = get_interview_manager()
        
        interviews = interview_mgr.list_interviews(
            interviewer_id=args.interviewer,
            candidate_email=args.candidate,
            status=args.status,
            limit=args.limit or 50
        )
        
        if not interviews:
            print("No interviews found.")
            return
        
        print(f"\n{'='*80}")
        print(f"📋 Interviews ({len(interviews)} found)")
        print(f"{'='*80}")
        print(f"{'ID':<20} {'Candidate':<25} {'Status':<15} {'Problem':<15} {'Language':<10}")
        print(f"{'-'*80}")
        
        for interview in interviews:
            print(f"{interview.id:<20} {interview.candidate_email:<25} {interview.status:<15} {interview.problem_id:<15} {interview.language:<10}")
        
        print(f"{'='*80}\n")
        
        return interviews
        
    except Exception as e:
        print(f"❌ Error listing interviews: {e}", file=sys.stderr)
        sys.exit(1)


def get_observer(args):
    """Get observer view for an interview"""
    try:
        interview_mgr = get_interview_manager()
        
        observer_data = interview_mgr.get_observer_view(args.interview_id)
        
        print(f"\n{'='*60}")
        print(f"👁️ Observer View")
        print(f"{'='*60}")
        print(f"Interview ID:   {observer_data['interview_id']}")
        print(f"Candidate:      {observer_data['candidate_email']}")
        print(f"Status:         {observer_data['status']}")
        print(f"Problem:        {observer_data.get('problem', {}).get('title', 'N/A')}")
        print(f"Session Active: {observer_data.get('session_active', False)}")
        print(f"\nObserver Link:  /interviews/{observer_data['interview_id']}/observer")
        print(f"Observer Token: {observer_data['observer_token']}")
        print(f"{'='*60}\n")
        
        # Optionally open in browser
        if args.open:
            observer_url = f"{API_BASE}/interviews/{observer_data['interview_id']}/observer"
            print(f"Opening observer view in browser...")
            webbrowser.open(observer_url)
        
        return observer_data
        
    except Exception as e:
        print(f"❌ Error getting observer view: {e}", file=sys.stderr)
        sys.exit(1)


def list_problems(args):
    """List available interview problems"""
    try:
        interview_mgr = get_interview_manager()
        
        problems = list(interview_mgr.problems.values())
        
        print(f"\n{'='*80}")
        print(f"📝 Available Interview Problems ({len(problems)})")
        print(f"{'='*80}")
        print(f"{'ID':<25} {'Title':<30} {'Difficulty':<12} {'Language':<15}")
        print(f"{'-'*80}")
        
        for problem in problems:
            print(f"{problem.id:<25} {problem.title:<30} {problem.difficulty:<12} {problem.language:<15}")
        
        print(f"{'='*80}\n")
        
        return problems
        
    except Exception as e:
        print(f"❌ Error listing problems: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='sshBox Interview CLI - Schedule and manage technical interviews',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s schedule --candidate alice@example.com --interviewer bob@company.com
  %(prog)s start --interview-id int_abc123
  %(prog)s complete --interview-id int_abc123 --score 85 --feedback "Excellent!"
  %(prog)s list --status scheduled
  %(prog)s observer --interview-id int_abc123 --open
  %(prog)s problems
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Schedule command
    schedule_parser = subparsers.add_parser('schedule', help='Schedule a new interview')
    schedule_parser.add_argument('--candidate', '-c', required=True, help='Candidate email')
    schedule_parser.add_argument('--interviewer', '-i', required=True, help='Interviewer email')
    schedule_parser.add_argument('--problem', '-p', default='two_sum', help='Problem ID (default: two_sum)')
    schedule_parser.add_argument('--language', '-l', default='python', help='Programming language')
    schedule_parser.add_argument('--ttl', type=int, default=3600, help='Session TTL in seconds')
    schedule_parser.add_argument('--open', action='store_true', help='Open observer view in browser')
    
    # Start command
    start_parser = subparsers.add_parser('start', help='Start an interview session')
    start_parser.add_argument('--interview-id', '-i', required=True, help='Interview ID')
    start_parser.add_argument('--open', action='store_true', help='Open web terminal in browser')
    
    # Complete command
    complete_parser = subparsers.add_parser('complete', help='Complete an interview')
    complete_parser.add_argument('--interview-id', '-i', required=True, help='Interview ID')
    complete_parser.add_argument('--score', '-s', type=int, help='Score 0-100')
    complete_parser.add_argument('--feedback', '-f', help='Interviewer feedback')
    
    # Cancel command
    cancel_parser = subparsers.add_parser('cancel', help='Cancel an interview')
    cancel_parser.add_argument('--interview-id', '-i', required=True, help='Interview ID')
    cancel_parser.add_argument('--reason', '-r', help='Cancellation reason')
    
    # List command
    list_parser = subparsers.add_parser('list', help='List interviews')
    list_parser.add_argument('--interviewer', '-i', help='Filter by interviewer')
    list_parser.add_argument('--candidate', '-c', help='Filter by candidate')
    list_parser.add_argument('--status', '-s', help='Filter by status')
    list_parser.add_argument('--limit', '-l', type=int, default=50, help='Max results')
    
    # Observer command
    observer_parser = subparsers.add_parser('observer', help='Get observer view')
    observer_parser.add_argument('--interview-id', '-i', required=True, help='Interview ID')
    observer_parser.add_argument('--open', action='store_true', help='Open in browser')
    
    # Problems command
    problems_parser = subparsers.add_parser('problems', help='List available problems')
    
    args = parser.parse_args()
    
    if args.command == 'schedule':
        schedule_interview(args)
    elif args.command == 'start':
        start_interview(args)
    elif args.command == 'complete':
        complete_interview(args)
    elif args.command == 'cancel':
        cancel_interview(args)
    elif args.command == 'list':
        list_interviews(args)
    elif args.command == 'observer':
        get_observer(args)
    elif args.command == 'problems':
        list_problems(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
