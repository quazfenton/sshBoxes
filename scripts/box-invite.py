#!/usr/bin/env python3
"""
Enhanced sshBox CLI tool
Provides functionality for creating invites, connecting to boxes, and managing sessions

Usage:
  Create invite: ./box-invite.py create --secret SECRET --profile dev --ttl 600
  Client helper: ./box-invite.py connect --token <TOKEN> --gateway http://localhost:8080 --privkey-path ./id_box
  List sessions: ./box-invite.py sessions --gateway http://localhost:8080
  Destroy session: ./box-invite.py destroy --session-id <SESSION_ID> --gateway http://localhost:8080
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
from datetime import datetime


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


def client_connect(token, gateway, privkey_path=None):
    """
    Connect to a box using an invite token
    """
    # Generate keypair locally
    if privkey_path is None:
        privkey_path = "./id_box"

    pubkey_path = privkey_path + ".pub"

    try:
        # Check if key already exists
        if os.path.exists(privkey_path):
            print(f"Using existing key: {privkey_path}")
            if not os.path.exists(pubkey_path):
                print(f"Error: Private key exists but public key {pubkey_path} is missing")
                return
            with open(pubkey_path, 'r') as f:
                pubkey = f.read().strip()
        else:
            print(f"Generating new keypair: {privkey_path}")
            result = subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", privkey_path, "-N", ""],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if result.returncode != 0:
                print(f"Error generating SSH key: {result.stderr.decode()}")
                return

            if not os.path.exists(pubkey_path):
                print(f"Error: SSH key generation failed, public key {pubkey_path} not found")
                return

            with open(pubkey_path, 'r') as f:
                pubkey = f.read().strip()

        # Parse token to extract profile and ttl
        token_parts = token.split(':')
        if len(token_parts) >= 3:
            token_profile = token_parts[0]
            token_ttl = int(token_parts[1])
        else:
            print(f"Error: Invalid token format")
            return

        # POST to gateway
        try:
            resp = requests.post(f"{gateway}/request",
                               json={"token": token, "pubkey": pubkey, "profile": token_profile, "ttl": token_ttl},
                               timeout=30)  # Increased timeout
        except requests.exceptions.RequestException as e:
            print(f"Gateway connection error: {e}")
            return

        if resp.status_code != 200:
            print(f"Gateway error (status {resp.status_code}): {resp.text}")
            return

        try:
            info = resp.json()
        except json.JSONDecodeError:
            print(f"Gateway returned invalid JSON: {resp.text}")
            return

        if 'error' in info:
            print(f"Gateway error: {info['error']}")
            return

        host = info.get('host')
        port = info.get('port')
        user = info.get('user')
        session_id = info.get('session_id', 'unknown')

        if not all([host, port, user]):
            print(f"Gateway returned incomplete connection info: {info}")
            return

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
        try:
            with open(details_file, 'w') as f:
                json.dump(conn_details, f, indent=2)
            print(f"Connection details saved to: {details_file}")
        except IOError as e:
            print(f"Warning: Could not save connection details to {details_file}: {e}")

        # Connect via ssh
        ssh_cmd = ["ssh", "-i", privkey_path, f"{user}@{host}", "-p", str(port)]
        print("Executing:", " ".join(ssh_cmd))

        try:
            os.execvp("ssh", ssh_cmd)
        except OSError as e:
            print(f"Error executing SSH command: {e}")
            # If os.execvp fails, try subprocess (though this won't fully replace the process)
            subprocess.run(ssh_cmd)

    except Exception as e:
        print(f"Unexpected error during connection: {e}")
        import traceback
        traceback.print_exc()


def list_sessions(gateway, status_filter=None):
    """
    List active sessions from the gateway
    """
    try:
        resp = requests.get(f"{gateway}/sessions")
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


def destroy_session(gateway, session_id):
    """
    Request to destroy a specific session
    """
    try:
        resp = requests.post(f"{gateway}/destroy",
                           json={"session_id": session_id})
        if resp.status_code != 200:
            print(f"Error destroying session: {resp.text}")
            return

        result = resp.json()
        print(f"Session {session_id} destruction requested: {result}")

    except requests.exceptions.RequestException as e:
        print(f"Error connecting to gateway: {e}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description='sshBox CLI tool')
    sub = p.add_subparsers(dest='cmd', help='Available commands')

    # Create invite subcommand
    create_parser = sub.add_parser('create', help='Create an invite token')
    create_parser.add_argument('--secret', required=True, help='Secret for token signing')
    create_parser.add_argument('--profile', default='dev',
                              help='Profile for the box (dev, debug, secure-shell)')
    create_parser.add_argument('--ttl', type=int, default=600,
                              help='Time-to-live in seconds')
    create_parser.add_argument('--recipient',
                              help='Email or identifier of the recipient')
    create_parser.add_argument('--notes',
                              help='Additional notes about the invite')

    # Connect subcommand
    connect_parser = sub.add_parser('connect', help='Connect to a box')
    connect_parser.add_argument('--token', required=True, help='Invite token')
    connect_parser.add_argument('--gateway', default='http://localhost:8080', help='Gateway URL')
    connect_parser.add_argument('--privkey-path', default='./id_box', help='Path to store private key')

    # Sessions subcommand
    sessions_parser = sub.add_parser('sessions', help='List sessions')
    sessions_parser.add_argument('--gateway', default='http://localhost:8080', help='Gateway URL')
    sessions_parser.add_argument('--status', help='Filter by status (active, destroyed, etc.)')

    # Destroy subcommand
    destroy_parser = sub.add_parser('destroy', help='Destroy a session')
    destroy_parser.add_argument('--session-id', required=True, help='Session ID to destroy')
    destroy_parser.add_argument('--gateway', default='http://localhost:8080', help='Gateway URL')

    args = p.parse_args()

    if args.cmd == 'create':
        create_invite(args.secret, args.profile, args.ttl, args.recipient, args.notes)
    elif args.cmd == 'connect':
        client_connect(args.token, args.gateway, args.privkey_path)
    elif args.cmd == 'sessions':
        list_sessions(args.gateway, args.status)
    elif args.cmd == 'destroy':
        destroy_session(args.gateway, args.session_id)
    else:
        p.print_help()