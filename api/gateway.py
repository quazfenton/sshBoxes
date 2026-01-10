#!/usr/bin/env python3
"""
A proper HTTP gateway for sshBox using Flask
"""
import os
import hmac
import hashlib
import time
import json
import subprocess
import threading
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

# Configuration
GATEWAY_SECRET = os.environ.get('GATEWAY_SECRET', 'replace-with-secret')
PROVISIONER_PATH = os.environ.get('PROVISIONER_PATH', './scripts/box-provision.sh')

def validate_token(token):
    """Validate HMAC token"""
    try:
        parts = token.split(':')
        if len(parts) != 4:
            return False
        
        profile, ttl, timestamp, signature = parts
        expected_payload = f"{profile}:{ttl}:{timestamp}"
        expected_signature = hmac.new(
            GATEWAY_SECRET.encode(), 
            expected_payload.encode(), 
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(signature, expected_signature)
    except:
        return False

def schedule_destroy(container_name, metadata_file, ttl):
    """Schedule container destruction after TTL"""
    def destroy_task():
        time.sleep(ttl)
        try:
            subprocess.run(['./scripts/box-destroy.sh', container_name, metadata_file], 
                         check=True, capture_output=True)
        except subprocess.CalledProcessError:
            print(f"Failed to destroy container {container_name}")
    
    thread = threading.Thread(target=destroy_task)
    thread.daemon = True
    thread.start()

@app.route('/request', methods=['POST'])
def handle_request():
    try:
        data = request.get_json()
        
        token = data.get('token')
        pubkey = data.get('pubkey')
        profile = data.get('profile', 'dev')
        ttl = int(data.get('ttl', 1800))
        
        # Validate token
        if not validate_token(token):
            return jsonify({'error': 'Invalid token'}), 403
        
        # Generate session ID
        session_id = str(int(time.time() * 1000000))  # microsecond precision
        
        # Call provisioner script
        result = subprocess.run([
            PROVISIONER_PATH, 
            session_id, 
            pubkey, 
            profile, 
            str(ttl)
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            return jsonify({'error': f'Provisioning failed: {result.stderr}'}), 500
        
        # Parse provisioner output
        try:
            connection_info = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            return jsonify({'error': 'Invalid response from provisioner'}), 500
        
        # Schedule destruction
        metadata_file = f"/tmp/{session_id}.json"
        schedule_destroy(connection_info['session_id'], metadata_file, ttl)
        
        return jsonify(connection_info)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('GATEWAY_PORT', 8080)))