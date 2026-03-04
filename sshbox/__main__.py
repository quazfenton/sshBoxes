#!/usr/bin/env python3
"""
sshBox - Main Entry Point

Unified CLI for sshBox operations

Usage:
    python -m sshbox gateway      - Start SSH gateway
    python -m sshbox web          - Start web terminal
    python -m sshbox interview    - Start interview API
    python -m sshbox all          - Start all services
"""
import os
import sys
import argparse
import threading
import time
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def start_gateway():
    """Start the SSH gateway"""
    print("Starting SSH Gateway on port 8080...")
    os.environ.setdefault('GATEWAY_SECRET', 'MyStr0ng!Secret@Key#2024')
    os.environ.setdefault('GATEWAY_PORT', '8080')
    
    from api.gateway_fastapi import app
    import uvicorn
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="info"
    )


def start_web():
    """Start the web terminal bridge"""
    print("Starting Web Terminal Bridge on port 3000...")
    os.environ.setdefault('WEB_PORT', '3000')
    os.environ.setdefault('GATEWAY_URL', 'http://localhost:8080')
    
    from web.websocket_bridge import app
    import uvicorn
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=3000,
        log_level="info"
    )


def start_interview():
    """Start the interview API"""
    print("Starting Interview API on port 8083...")
    os.environ.setdefault('INTERVIEW_API_PORT', '8083')
    os.environ.setdefault('GATEWAY_URL', 'http://localhost:8080')
    
    from api.interview_api import app
    import uvicorn
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8083,
        log_level="info"
    )


def start_all():
    """Start all services"""
    print("Starting all sshBox services...")
    print("=" * 60)
    
    # Start services in threads
    threads = []
    
    # Gateway
    gateway_thread = threading.Thread(target=start_gateway, daemon=True)
    gateway_thread.start()
    threads.append(gateway_thread)
    time.sleep(1)
    
    # Web terminal
    web_thread = threading.Thread(target=start_web, daemon=True)
    web_thread.start()
    threads.append(web_thread)
    time.sleep(1)
    
    # Interview API
    interview_thread = threading.Thread(target=start_interview, daemon=True)
    interview_thread.start()
    threads.append(interview_thread)
    
    print("=" * 60)
    print("All services started:")
    print("  - SSH Gateway:      http://localhost:8080")
    print("  - Web Terminal:     http://localhost:3000")
    print("  - Interview API:    http://localhost:8083")
    print("=" * 60)
    print("\nPress Ctrl+C to stop all services\n")
    
    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")


def main():
    parser = argparse.ArgumentParser(
        description='sshBox - The Interview Operating System',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s gateway      - Start SSH gateway only
  %(prog)s web          - Start web terminal only
  %(prog)s interview    - Start interview API only
  %(prog)s all          - Start all services
        """
    )
    
    parser.add_argument(
        'service',
        choices=['gateway', 'web', 'interview', 'all'],
        help='Service to start'
    )
    
    args = parser.parse_args()
    
    if args.service == 'gateway':
        start_gateway()
    elif args.service == 'web':
        start_web()
    elif args.service == 'interview':
        start_interview()
    elif args.service == 'all':
        start_all()


if __name__ == '__main__':
    main()
