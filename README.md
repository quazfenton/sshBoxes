# sshBox - The Interview Operating System

**Purpose-built ephemeral environments for technical hiring and secure development**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)](https://fastapi.tiangolo.com/)
[![Product Hunt](https://img.shields.io/badge/Product-Hunt-orange.svg)](https://producthunt.com)

---

## 🎯 What is sshBox?

sshBox provides **instantly-provisioned, secure, short-lived Linux environments** accessible via SSH or web browser. Originally built for debugging and experiments, it's now the **Interview Operating System** - purpose-built for technical hiring with observer modes, recording, and scoring.

### Key Value Propositions

| For | Benefit |
|-----|---------|
| **Recruiting Teams** | No more lost candidates to environment setup issues |
| **Engineering Managers** | See candidates code in real-time with full recordings |
| **Candidates** | Zero setup - just click and code |
| **Security Teams** | Full audit trail, ephemeral credentials, auto-destroy |

---

## 🚀 Quick Start

### 1. Start Services

```bash
# Start all services (Docker required)
docker-compose up -d

# Start web terminal bridge
python web/websocket_bridge.py

# Start interview API
python api/interview_api.py
```

### 2. Schedule Your First Interview

```bash
# Via CLI
./scripts/sshbox-interview.py schedule \
    --candidate candidate@example.com \
    --interviewer interviewer@company.com \
    --problem two_sum \
    --language python

# Via API
curl -X POST http://localhost:8083/interviews/schedule \
    -H "Content-Type: application/json" \
    -d '{
        "candidate_email": "candidate@example.com",
        "interviewer_email": "interviewer@company.com",
        "problem_id": "two_sum",
        "language": "python"
    }'
```

### 3. Connect via Web Terminal

Open browser to: `http://localhost:3000/static/index.html`

---

## 📋 Features

### 🎓 Interview Mode

Purpose-built for technical interviews with everything you need:

- ✅ **Pre-configured Problems** - Two Sum, Valid Parentheses, Merge Intervals + custom
- ✅ **Observer View** - Watch candidates code in real-time
- ✅ **Interview Chat** - Communicate without leaving the terminal
- ✅ **Session Recording** - Full terminal recording for later review
- ✅ **Scoring System** - 0-100 scale with feedback
- ✅ **REST API** - Full API for integration with ATS systems
- ✅ **CLI Interface** - Command-line management for power users

### 🌐 Web Terminal

Browser-based SSH access without any client installation:

- ✅ **Xterm.js Terminal** - Full terminal emulation with 256 colors
- ✅ **WebSocket Bridge** - Low-latency real-time streaming
- ✅ **Shareable Links** - Send observer links to stakeholders
- ✅ **Auto-Recording** - All sessions recorded automatically
- ✅ **Mobile Friendly** - Works on tablets and phones

### 🔒 Enterprise Security

Production-ready security features:

- ✅ **Token Authentication** - HMAC-SHA256 with constant-time validation
- ✅ **Input Validation** - All inputs validated and sanitized
- ✅ **SQL Injection Prevention** - Parameterized queries only
- ✅ **Path Traversal Prevention** - Safe path validation
- ✅ **Command Injection Prevention** - Shell script hardening
- ✅ **Quota Management** - Per-user and per-org limits
- ✅ **Policy Engine** - OPA integration for fine-grained access control
- ✅ **Circuit Breakers** - Fault tolerance and graceful degradation
- ✅ **Session Recording** - Full audit trail
- ✅ **Rate Limiting** - Per-endpoint rate limits

### 📊 Monitoring & Observability

Full visibility into system health:

- ✅ **Prometheus Metrics** - Comprehensive metrics collection
- ✅ **Grafana Dashboards** - Pre-built dashboards
- ✅ **20+ Alert Rules** - Proactive alerting for issues
- ✅ **Health Endpoints** - `/health` on all services
- ✅ **Structured Logging** - JSON logs for production

### 🏗️ Infrastructure

Flexible deployment options:

- ✅ **Docker Containers** - Fast, lightweight provisioning
- ✅ **Firecracker MicroVMs** - Stronger isolation when needed
- ✅ **Production HA** - High-availability docker-compose
- ✅ **PostgreSQL** - Persistent session storage
- ✅ **Redis** - Caching and coordination
- ✅ **OPA** - Policy engine integration

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           sshBox Architecture                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  CLIENTS                                                                 │
│  ┌──────────────┐  ┌─────────────────┐  ┌─────────────────┐            │
│  │ SSH Client   │  │ Web Terminal    │  │ Interview CLI   │            │
│  │              │  │ (Xterm.js)      │  │                 │            │
│  └──────┬───────┘  └────────┬────────┘  └────────┬────────┘            │
│         │                   │                    │                      │
│         │ SSH               │ WebSocket          │ HTTP                 │
│         │                   │                    │                      │
│         └───────────────────┼────────────────────┘                      │
│                             │                                           │
│                             v                                           │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                      API Gateway Layer                            │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │  │
│  │  │ SSH Gateway  │  │WebSocket     │  │ Interview    │           │  │
│  │  │ (FastAPI)    │  │Bridge        │  │API           │           │  │
│  │  │ :8080        │  │(:3000)       │  │(:8083)       │           │  │
│  │  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘           │  │
│  └─────────┼─────────────────┼─────────────────┼───────────────────┘  │
│            │                 │                 │                       │
│            └─────────────────┼─────────────────┘                       │
│                              │                                         │
│                              v                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                      Core Services                                │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │  │
│  │  │ Provisioner  │  │ Policy       │  │ Quota        │           │  │
│  │  │              │  │ Engine (OPA) │  │ Manager      │           │  │
│  │  └──────────────┘  └──────────────┘  └──────────────┘           │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │  │
│  │  │ Session      │  │ Circuit      │  │ Metrics      │           │  │
│  │  │ Recorder     │  │ Breakers     │  │ Collector    │           │  │
│  │  └──────────────┘  └──────────────┘  └──────────────┘           │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                              │                                         │
│                              v                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                      Runtime Layer                                │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │  │
│  │  │ Docker       │  │ Firecracker  │  │ Interview    │           │  │
│  │  │ Containers   │  │ MicroVMs     │  │ Sessions     │           │  │
│  │  └──────────────┘  └──────────────┘  └──────────────┘           │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                              │                                         │
│                              v                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                      Data Layer                                   │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │  │
│  │  │ PostgreSQL   │  │ Redis        │  │ Recordings   │           │  │
│  │  │ (Sessions)   │  │ (Cache)      │  │ (Files)      │           │  │
│  │  └──────────────┘  └──────────────┘  └──────────────┘           │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 📦 Installation

### Self-Hosted (Development)

```bash
# Clone repository
git clone https://github.com/sshbox/sshbox.git
cd sshbox

# Install dependencies
pip install -r requirements.txt

# Start services
docker-compose up -d

# Start web terminal
python web/websocket_bridge.py

# Start interview API
python api/interview_api.py
```

### Self-Hosted (Production)

```bash
# Use production docker-compose
docker-compose -f docker-compose.prod.yml up -d

# Access services
# Gateway: http://localhost:8080
# Web Terminal: http://localhost:3000
# Interview API: http://localhost:8083
# Grafana: http://localhost:3001
# Prometheus: http://localhost:9090
```

### Managed Service

Coming soon at [sshbox.io](https://sshbox.io)

---

## 🎓 Interview Mode

### Scheduling an Interview

#### Via CLI

```bash
# Schedule interview
./scripts/sshbox-interview.py schedule \
    --candidate candidate@example.com \
    --interviewer interviewer@company.com \
    --problem two_sum \
    --language python \
    --ttl 3600

# Start interview session
./scripts/sshbox-interview.py start --interview-id int_abc123

# Complete with score
./scripts/sshbox-interview.py complete \
    --interview-id int_abc123 \
    --score 85 \
    --feedback "Excellent problem-solving skills!"
```

#### Via API

```bash
# Schedule
curl -X POST http://localhost:8083/interviews/schedule \
    -H "Content-Type: application/json" \
    -d '{
        "candidate_email": "candidate@example.com",
        "interviewer_email": "interviewer@company.com",
        "problem_id": "two_sum",
        "language": "python"
    }'

# Start
curl -X POST http://localhost:8083/interviews/int_abc123/start

# Complete
curl -X POST http://localhost:8083/interviews/int_abc123/complete \
    -H "Content-Type: application/json" \
    -d '{"score": 85, "feedback": "Great work!"}'
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

problem = InterviewProblem(
    id="custom_binary_search",
    title="Binary Search",
    description="Implement binary search...",
    difficulty="medium",
    language="python",
    starter_code="def binary_search(arr, target):\n    pass\n",
    test_cases=[
        {"input": [[1, 2, 3, 4, 5], 3], "expected": 2}
    ],
    expected_output=[2]
)

manager.add_custom_problem(problem)
```

---

## 🌐 Web Terminal

### Access Methods

#### Browser

```
http://localhost:3000/static/index.html
```

#### With Token

```
http://localhost:3000/static/index.html?token=YOUR_TOKEN
```

#### Observer View

```
http://localhost:3000/static/index.html?session=SESSION_ID&observer=true
```

### Features

- **Full Terminal Emulation** - Xterm.js with 256 colors
- **Real-time Streaming** - WebSocket-based low latency
- **Session Recording** - Automatic recording of all sessions
- **Shareable Links** - Generate observer links
- **Interview Chat** - Built-in messaging

---

## 🔒 Security Model

### Authentication

- **HMAC Token Validation** - SHA-256 signatures
- **Constant-Time Comparison** - Prevents timing attacks
- **Token Expiration** - Configurable TTL (default: 5 min)
- **Replay Prevention** - Timestamp validation

### Authorization

- **Policy Engine (OPA)** - Fine-grained access control
- **Quota Management** - Per-user and per-org limits
- **Role-Based Access** - default, premium, admin, trial roles
- **Profile Restrictions** - Whitelist-based profile access

### Input Validation

- **Session ID Validation** - Alphanumeric only
- **SSH Key Validation** - Format and option checking
- **Path Traversal Prevention** - Safe path resolution
- **Command Injection Prevention** - Shell script hardening
- **SQL Injection Prevention** - Parameterized queries

### Audit & Compliance

- **Session Recording** - Full terminal recording
- **Audit Logs** - All actions logged
- **Metrics Collection** - Prometheus-compatible
- **SOC 2 Ready** - Enterprise compliance features

---

## 📊 Monitoring

### Prometheus Metrics

| Metric | Description |
|--------|-------------|
| `sshbox_requests_total` | Total HTTP requests |
| `sshbox_sessions_created` | Sessions created |
| `sshbox_sessions_destroyed` | Sessions destroyed |
| `sshbox_avg_provision_time` | Average provision time |
| `sshbox_errors_total` | Error count by type |
| `sshbox_circuit_breaker_state` | Circuit breaker states |

### Grafana Dashboards

Pre-built dashboards for:
- Session metrics
- Request latency
- Error rates
- Circuit breaker status
- Database performance
- Redis metrics

### Alerts

20+ alert rules including:
- High error rate
- High latency
- Circuit breaker open
- Database connection issues
- Redis memory usage
- Recording storage low
- Token validation failures

---

## 🔌 Integrations

### ATS Systems

- **Greenhouse** - Auto-schedule interviews
- **Lever** - Candidate pipeline
- **Workday** - Enterprise recruiting

### Developer Tools

- **GitHub** - PR review environments
- **GitLab** - MR review
- **VS Code** - Remote development (planned)

### Communication

- **Slack** - Interview notifications
- **Microsoft Teams** - Status updates
- **Email** - Candidate invites

### Identity

- **Okta** - SSO integration (planned)
- **Auth0** - Authentication (planned)
- **Azure AD** - Enterprise SSO (planned)

---

## 📊 Pricing

| Tier | Price | Features |
|------|-------|----------|
| **Developer** | Free | 10 boxes/month, 30min TTL, basic recording |
| **Pro** | $10/mo | 100 boxes/month, 2hr TTL, full recording |
| **Team** | $50/mo | Unlimited boxes, collaboration, observer mode |
| **Enterprise** | Custom | SSO, audit logs, on-premise, SLA |

---

## 📖 Documentation

| Document | Description |
|----------|-------------|
| [Web Terminal & Interview Guide](docs/WEB_TERMINAL_INTERVIEW_GUIDE.md) | Complete user guide |
| [Implementation Summary](docs/IMPLEMENTATION_SUMMARY.md) | Technical implementation details |
| [Final Review Summary](docs/FINAL_REVIEW_SUMMARY.md) | Review findings and recommendations |
| [Comprehensive Improvement Plan](docs/COMPREHENSIVE_IMPROVEMENT_PLAN.md) | Technical improvement plan |
| [Firecracker Implementation](docs/firecracker_implementation.md) | MicroVM setup guide |
| [Performance Optimization](docs/performance_optimization.md) | Performance tuning |

---

## 🧪 Testing

```bash
# Run all tests
pytest tests/ -v

# Security tests
pytest tests/test_security.py -v

# With coverage
pytest tests/ --cov=api --cov-report=html
```

---

## 🤝 Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

### Development Setup

```bash
# Clone and setup
git clone https://github.com/sshbox/sshbox.git
cd sshbox
pip install -r requirements.txt

# Run tests
pytest tests/ -v

# Start development services
docker-compose up -d
```

---

## 📝 License

MIT License - see [LICENSE](LICENSE) for details.

---

## 🙋 Support

| Channel | Link |
|---------|------|
| Documentation | https://docs.sshbox.io |
| GitHub Issues | https://github.com/sshbox/sshbox/issues |
| Discord | https://discord.gg/sshbox |
| Twitter | @sshbox_io |
| Email | support@sshbox.io |

---

## 📈 Roadmap

### Q2 2026
- [ ] VS Code integration (code-server)
- [ ] Multi-user collaboration
- [ ] Plugin system for profiles

### Q3 2026
- [ ] Kubernetes operator
- [ ] SSO integration (SAML, OIDC)
- [ ] Compliance dashboard

### Q4 2026
- [ ] Mobile app (iOS/Android)
- [ ] Marketplace for profiles/problems
- [ ] Cloud provider integrations (AWS, GCP)

---

*Last updated: 2026-03-03*  
*Version: 2.0.0*
