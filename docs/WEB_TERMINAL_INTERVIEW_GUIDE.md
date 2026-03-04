# sshBox Web Terminal & Interview Mode - Getting Started Guide

## Table of Contents

1. [Quick Start](#quick-start)
2. [Web Terminal](#web-terminal)
3. [Interview Mode](#interview-mode)
4. [API Reference](#api-reference)
5. [CLI Reference](#cli-reference)
6. [Troubleshooting](#troubleshooting)

---

## Quick Start

### 1. Start All Services

```bash
# Start the main gateway
docker-compose up -d gateway

# Start the web terminal bridge
python web/websocket_bridge.py

# Start the interview API
python api/interview_api.py
```

### 2. Access Web Terminal

Open your browser to:
```
http://localhost:3000/static/index.html
```

### 3. Create Your First Interview

```bash
# Schedule interview
./scripts/sshbox-interview.py schedule \
    --candidate candidate@example.com \
    --interviewer interviewer@company.com \
    --problem two_sum \
    --language python

# Start the interview session
./scripts/sshbox-interview.py start --interview-id INTERVIEW_ID

# Get observer link
./scripts/sshbox-interview.py observer --interview-id INTERVIEW_ID --open
```

---

## Web Terminal

### Overview

The sshBox Web Terminal provides browser-based SSH access without requiring any client installation. It uses WebSocket to bridge terminal I/O between the browser and SSH sessions.

### Features

- **Full Terminal Emulation** - Xterm.js-based terminal with 256 colors
- **Real-time Streaming** - Low-latency WebSocket connection
- **Session Recording** - All sessions are automatically recorded
- **Shareable Links** - Generate observer links for collaboration
- **Interview Mode** - Built-in chat and observer view

### Architecture

```
[Browser] <--WebSocket--> [WebSocket Bridge] <--SSH--> [sshBox Container]
     |                           |
     |--> Xterm.js               |--> PTY Management
     |--> Terminal I/O           |--> Session Recording
```

### Usage

#### Via Web Interface

1. Open `http://localhost:3000/static/index.html`
2. Click "Connect" or enter an invite token
3. Terminal connects automatically

#### Via URL Parameters

```bash
# Connect with token
http://localhost:3000/static/index.html?token=YOUR_TOKEN

# Observer view
http://localhost:3000/static/index.html?session=SESSION_ID&observer=true
```

### Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `WEB_PORT` | 3000 | Port for web server |
| `GATEWAY_URL` | http://localhost:8080 | Gateway API URL |
| `ALLOWED_ORIGINS` | * | CORS allowed origins |

---

## Interview Mode

### Overview

Interview Mode provides purpose-built functionality for technical interviews with observer views, pre-configured problems, and automatic recording.

### Key Features

- **Pre-configured Problems** - Built-in coding problems (Two Sum, Valid Parentheses, etc.)
- **Observer View** - Real-time monitoring of candidate sessions
- **Interview Chat** - Communication channel between interviewer and candidate
- **Session Recording** - Automatic recording for later review
- **Scoring & Feedback** - Built-in evaluation system

### Workflow

```
1. Schedule Interview
   ↓
2. Send invite to candidate
   ↓
3. Candidate connects (web or SSH)
   ↓
4. Interviewer observes in real-time
   ↓
5. Complete with score/feedback
   ↓
6. Recording saved for review
```

### Scheduling an Interview

#### Via CLI

```bash
./scripts/sshbox-interview.py schedule \
    --candidate candidate@example.com \
    --interviewer interviewer@company.com \
    --problem two_sum \
    --language python \
    --ttl 3600
```

#### Via API

```bash
curl -X POST http://localhost:8083/interviews/schedule \
    -H "Content-Type: application/json" \
    -d '{
        "candidate_email": "candidate@example.com",
        "interviewer_email": "interviewer@company.com",
        "problem_id": "two_sum",
        "language": "python"
    }'
```

### Available Problems

| ID | Title | Difficulty | Language |
|----|-------|------------|----------|
| `two_sum` | Two Sum | Easy | Python |
| `valid_parentheses` | Valid Parentheses | Easy | Python |
| `merge_intervals` | Merge Intervals | Medium | Python |

### Adding Custom Problems

```python
from api.interview_mode import get_interview_manager, InterviewProblem

manager = get_interview_manager()

custom_problem = InterviewProblem(
    id="custom_problem",
    title="Custom Problem",
    description="Problem description...",
    difficulty="medium",
    language="python",
    starter_code="def solve():\n    pass\n",
    test_cases=[{"input": [1, 2], "expected": 3}],
    expected_output=[3]
)

manager.add_custom_problem(custom_problem)
```

### Observer View

The observer view allows interviewers to:

- Watch candidate's terminal in real-time
- Send chat messages
- View problem details
- Access session recording

```bash
# Get observer link
./scripts/sshbox-interview.py observer --interview-id INT_ID --open

# Or via API
curl http://localhost:8083/interviews/INT_ID/observer
```

---

## API Reference

### Interview Endpoints

#### POST /interviews/schedule

Schedule a new interview.

**Request:**
```json
{
    "candidate_email": "candidate@example.com",
    "interviewer_email": "interviewer@company.com",
    "problem_id": "two_sum",
    "language": "python",
    "ttl_seconds": 3600
}
```

**Response:**
```json
{
    "id": "int_abc123",
    "candidate_email": "candidate@example.com",
    "status": "scheduled",
    "observer_token": "token_xyz"
}
```

#### POST /interviews/{id}/start

Start an interview session.

**Response:**
```json
{
    "interview_id": "int_abc123",
    "session_id": "box_123",
    "host": "192.168.1.100",
    "port": 2222,
    "user": "boxuser",
    "observer_link": "/web/?session=box_123&observer=true"
}
```

#### POST /interviews/{id}/complete

Complete an interview with score and feedback.

**Request:**
```json
{
    "score": 85,
    "feedback": "Great problem-solving skills!"
}
```

#### GET /interviews

List interviews with filters.

**Query Parameters:**
- `interviewer_id` - Filter by interviewer
- `candidate_email` - Filter by candidate
- `status` - Filter by status
- `limit` - Max results (default: 50)

#### GET /problems

List all available interview problems.

---

## CLI Reference

### Commands

#### schedule

Schedule a new interview.

```bash
./sshbox-interview.py schedule \
    --candidate EMAIL \
    --interviewer EMAIL \
    [--problem ID] \
    [--language LANG] \
    [--ttl SECONDS] \
    [--open]
```

#### start

Start an interview session.

```bash
./sshbox-interview.py start \
    --interview-id ID \
    [--open]
```

#### complete

Complete an interview.

```bash
./sshbox-interview.py complete \
    --interview-id ID \
    [--score SCORE] \
    [--feedback TEXT]
```

#### cancel

Cancel a scheduled interview.

```bash
./sshbox-interview.py cancel \
    --interview-id ID \
    [--reason TEXT]
```

#### list

List interviews.

```bash
./sshbox-interview.py list \
    [--interviewer EMAIL] \
    [--candidate EMAIL] \
    [--status STATUS] \
    [--limit N]
```

#### observer

Get observer view.

```bash
./sshbox-interview.py observer \
    --interview-id ID \
    [--open]
```

#### problems

List available problems.

```bash
./sshbox-interview.py problems
```

---

## Troubleshooting

### Web Terminal Issues

#### "Connection failed"

1. Check that WebSocket bridge is running: `python web/websocket_bridge.py`
2. Verify gateway is accessible: `curl http://localhost:8080/health`
3. Check browser console for errors

#### "Session not found"

1. Verify session ID is correct
2. Check if session has expired
3. Ensure session was created successfully

### Interview Mode Issues

#### "Problem not found"

1. List available problems: `./sshbox-interview.py problems`
2. Verify problem ID spelling
3. Check if custom problem was loaded correctly

#### "Failed to start session"

1. Check gateway logs for errors
2. Verify Docker is running
3. Check resource availability (CPU, memory)

#### Recording not working

1. Verify recordings directory exists and is writable
2. Check if asciinema is installed
3. Review recorder logs

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `Connection refused` | Service not running | Start the service |
| `Session expired` | TTL exceeded | Create new session |
| `Invalid token` | Token malformed | Regenerate token |
| `Quota exceeded` | User limit reached | Wait or increase quota |

---

## Production Deployment

### Environment Variables

```bash
# Web Terminal
export WEB_PORT=3000
export GATEWAY_URL=http://gateway:8080

# Interview API
export INTERVIEW_API_PORT=8083
export INTERVIEW_STORAGE=/var/lib/sshbox/interviews
export GATEWAY_URL=http://gateway:8080

# Security
export ALLOWED_ORIGINS=https://yourdomain.com
export CORS_ENABLED=true
```

### Docker Compose (Production)

```yaml
version: '3.8'

services:
  web-terminal:
    build:
      context: .
      dockerfile: images/Dockerfile.web
    ports:
      - "3000:3000"
    environment:
      - GATEWAY_URL=http://gateway:8080
    depends_on:
      - gateway

  interview-api:
    build:
      context: .
      dockerfile: images/Dockerfile.api
    ports:
      - "8083:8083"
    volumes:
      - interview_data:/var/lib/sshbox/interviews
    environment:
      - GATEWAY_URL=http://gateway:8080
    depends_on:
      - gateway

volumes:
  interview_data:
```

---

## Support

- **Documentation:** https://docs.sshbox.io
- **GitHub Issues:** https://github.com/sshbox/sshbox/issues
- **Discord:** https://discord.gg/sshbox
- **Email:** support@sshbox.io
