#!/usr/bin/env python3
"""
Enhanced sshBox CLI tool
Provides functionality for creating invites, connecting to boxes, and managing sessions

Usage:
  Create invite: box invite --profile dev --ttl 600 --recipient "alice@example.com"
  Client connect: box connect --token <TOKEN> --gateway http://localhost:8080
  List sessions: box sessions --status active
  Destroy session: box destroy --session-id <SESSION_ID>
"""

import argparse
import hmac
import hashlib
import time
import json
import os
import subprocess
import tempfile
import requests
import sys
from datetime import datetime
from pathlib import Path


def create_invite(secret, profile='dev', ttl=600, recipient=None, notes=None):
    """
    Create an invite token with HMAC signature
    Format: profile:ttl:timestamp:recipient_hash:notes_hash:signature
    """
    ts = str(int(time.time()))
    recipient_part = hashlib.sha256(recipient.encode() if recipient else b'').hexdigest()[:12] if recipient else 'none'
    notes_part = hashlib.sha256(notes.encode() if notes else b'').hexdigest()[:12] if notes else 'none'
    
    payload = f"{profile}:{ttl}:{ts}:{recipient_part}:{notes_part}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    token = f"{payload}:{sig}"
    
    # Print token and additional info
    print(f"Invite Token: {token}")
    print(f"Profile: {profile}")
    print(f"TTL: {ttl} seconds ({ttl/60:.1f} minutes)")
    print(f"Created: {datetime.fromtimestamp(int(ts)).isoformat()}")
    if recipient:
        print(f"Recipient: {recipient}")
    if notes:
        print(f"Notes: {notes}")
    
    # Also return invite data for potential API use
    return {
        "token": token,
        "profile": profile,
        "ttl": ttl,
        "created_at": ts,
        "recipient": recipient,
        "notes": notes
    }


def client_connect(token, gateway_url, privkey_path=None, save_key=False):
    """
    Connect to a box using an invite token
    """
    # Generate keypair locally
    if privkey_path is None:
        privkey_path = "./id_box"
    
    pubkey_path = privkey_path + ".pub"
    
    # Check if key already exists
    if os.path.exists(privkey_path):
        print(f"Using existing key: {privkey_path}")
        with open(pubkey_path, 'r') as f:
            pubkey = f.read().strip()
    else:
        print(f"Generating new keypair: {privkey_path}")
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", privkey_path, "-N", ""], 
                      check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(pubkey_path, 'r') as f:
            pubkey = f.read().strip()
    
    # POST to gateway
    try:
        resp = requests.post(f"{gateway_url}/request", 
                           json={"token": token, "pubkey": pubkey, "profile": "dev", "ttl": 300},
                           timeout=10)
    except requests.exceptions.RequestException as e:
        print(f"Gateway error: {e}")
        return
    
    if resp.status_code != 200:
        print(f"Gateway error: {resp.text}")
        return
    
    info = resp.json()
    
    if 'error' in info:
        print(f"Gateway error: {info['error']}")
        return
    
    host = info['host']
    port = info['port']
    user = info['user']
    session_id = info.get('session_id', 'unknown')
    
    print(f"Session ID: {session_id}")
    print(f"Connecting to: {user}@{host}:{port}")
    
    # Save connection details to a temporary file for reference
    conn_details = {
        "session_id": session_id,
        "host": host,
        "port": port,
        "user": user,
        "private_key": privkey_path,
        "connected_at": datetime.utcnow().isoformat()
    }
    
    details_file = f"./session_{session_id}.json"
    with open(details_file, 'w') as f:
        json.dump(conn_details, f, indent=2)
    
    print(f"Connection details saved to: {details_file}")
    
    # Connect via ssh
    ssh_cmd = ["ssh", "-i", privkey_path, f"{user}@{host}", "-p", str(port)]
    print("Executing:", " ".join(ssh_cmd))
    
    try:
        os.execvp("ssh", ssh_cmd)
    except OSError:
        # If os.execvp fails, try subprocess (though this won't fully replace the process)
        subprocess.run(ssh_cmd)


def list_sessions(gateway_url, status_filter=None):
    """
    List active sessions from the gateway
    """
    try:
        resp = requests.get(f"{gateway_url}/sessions", timeout=10)
        if resp.status_code != 200:
            print(f"Error getting sessions: {resp.text}")
            return
        
        sessions = resp.json()
        
        if status_filter:
            sessions = [s for s in sessions if s.get('status') == status_filter]
        
        if not sessions:
            print("No sessions found")
            return
        
        print(f"Found {len(sessions)} session(s):")
        for session in sessions:
            print(f"- {session.get('session_id', 'unknown')} | "
                  f"{session.get('profile', 'unknown')} | "
                  f"Status: {session.get('status', 'unknown')} | "
                  f"Expires in: {session.get('time_left', 'unknown')}s")
    
    except requests.exceptions.RequestException as e:
        print(f"Error connecting to gateway: {e}")


def destroy_session(gateway_url, session_id):
    """
    Request to destroy a specific session
    """
    try:
        resp = requests.post(f"{gateway_url}/destroy",
                           json={"session_id": session_id}, timeout=10)
        if resp.status_code != 200:
            print(f"Error destroying session: {resp.text}")
            return
        
        result = resp.json()
        print(f"Session {session_id} destruction requested: {result}")
    
    except requests.exceptions.RequestException as e:
        print(f"Error connecting to gateway: {e}")


def main():
    parser = argparse.ArgumentParser(description='sshBox CLI tool')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Invite subcommand
    invite_parser = subparsers.add_parser('invite', help='Create an invite token')
    invite_parser.add_argument('--profile', default='dev', 
                              help='Profile for the box (dev, debug, secure-shell)')
    invite_parser.add_argument('--ttl', type=int, default=600, 
                              help='Time-to-live in seconds')
    invite_parser.add_argument('--recipient', 
                              help='Email or identifier of the recipient')
    invite_parser.add_argument('--notes', 
                              help='Additional notes about the invite')
    invite_parser.add_argument('--secret', required=True, 
                              help='Secret for token signing')
    
    # Connect subcommand
    connect_parser = subparsers.add_parser('connect', help='Connect to a box')
    connect_parser.add_argument('--token', required=True, 
                               help='Invite token')
    connect_parser.add_argument('--gateway', default='http://localhost:8080', 
                               help='Gateway URL')
    connect_parser.add_argument('--privkey-path', default='./id_box', 
                               help='Path to store private key')
    connect_parser.add_argument('--save-key', action='store_true', 
                               help='Save key even if it already exists')
    
    # Sessions subcommand
    sessions_parser = subparsers.add_parser('sessions', help='List sessions')
    sessions_parser.add_argument('--gateway', default='http://localhost:8080', 
                                help='Gateway URL')
    sessions_parser.add_argument('--status', 
                                help='Filter by status (active, destroyed, etc.)')
    
    # Destroy subcommand
    destroy_parser = subparsers.add_parser('destroy', help='Destroy a session')
    destroy_parser.add_argument('--session-id', required=True, 
                               help='Session ID to destroy')
    destroy_parser.add_argument('--gateway', default='http://localhost:8080', 
                               help='Gateway URL')
    
    args = parser.parse_args()
    
    if args.command == 'invite':
        create_invite(args.secret, args.profile, args.ttl, args.recipient, args.notes)
    elif args.command == 'connect':
        client_connect(args.token, args.gateway, args.privkey_path, args.save_key)
    elif args.command == 'sessions':
        list_sessions(args.gateway, args.status)
    elif args.command == 'destroy':
        destroy_session(args.gateway, args.session_id)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()