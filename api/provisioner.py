#!/usr/bin/env python3
"""
Provisioner service for sshBox
Manages the lifecycle of ephemeral containers
"""
import os
import json
import subprocess
import time
import threading
from datetime import datetime
from pathlib import Path
import redis
import psycopg2
from flask import Flask, request, jsonify

app = Flask(__name__)

# Connect to Redis for coordination
redis_client = redis.Redis(
    host=os.environ.get('REDIS_URL', 'redis://localhost:6379').split('//')[1].split(':')[0],
    port=int(os.environ.get('REDIS_URL', 'redis://localhost:6379').split('//')[1].split(':')[1]),
    decode_responses=True
)

# Connect to PostgreSQL for persistent storage
def get_db_connection():
    return psycopg2.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        database=os.environ.get('DB_NAME', 'sshbox'),
        user=os.environ.get('DB_USER', 'sshbox_user'),
        password=os.environ.get('DB_PASS', 'sshbox_pass')
    )

def schedule_destroy(container_name, session_id, ttl):
    """Schedule container destruction after TTL"""
    def destroy_task():
        time.sleep(ttl)
        try:
            subprocess.run(['./scripts/box-destroy.sh', container_name], 
                         check=True, capture_output=True)
            
            # Update session status in DB
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "UPDATE sessions SET status = 'destroyed', ended_at = %s WHERE session_id = %s",
                (datetime.utcnow().isoformat(), session_id)
            )
            conn.commit()
            cur.close()
            conn.close()
        except subprocess.CalledProcessError as e:
            print(f"Failed to destroy container {container_name}: {e}")
        except Exception as e:
            print(f"Error updating session status: {e}")

    thread = threading.Thread(target=destroy_task)
    thread.daemon = True
    thread.start()

@app.route('/provision', methods=['POST'])
def provision_container():
    try:
        data = request.get_json()
        
        session_id = data.get('session_id')
        pubkey = data.get('pubkey')
        
        if not session_id or not pubkey:
            return jsonify({'error': 'session_id and pubkey are required'}), 400
        profile = data.get('profile', 'dev')
        ttl = int(data.get('ttl', 1800))
        
        # Generate container name
        container_name = f"box_{session_id}"
        
        # Call the shell script to provision container
        result = subprocess.run([
            './scripts/box-provision.sh',
            session_id,
            pubkey,
            profile,
            str(ttl)
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            return jsonify({'error': f'Provisioning failed: {result.stderr}'}), 500
        
        # Parse the output from the script
        connection_info = json.loads(result.stdout.strip())
        
        # Store session metadata in DB
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO sessions 
            (session_id, container_name, ssh_host, ssh_port, ssh_user, profile, ttl, status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            session_id,
            container_name,
            connection_info.get('host'),
            connection_info.get('port'),
            connection_info.get('user'),
            profile,
            ttl,
            'active',
            datetime.utcnow().isoformat()
        ))
        conn.commit()
        cur.close()
        conn.close()
        
        # Schedule destruction
        schedule_destroy(container_name, session_id, ttl)
        
        return jsonify(connection_info)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8081)