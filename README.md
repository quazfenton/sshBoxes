# sshBox - Ephemeral SSH Boxes

Provide instantly-provisioned, secure, short-lived Linux bastions ("ephemeral boxes") accessible via a single SSH command (e.g., `ssh box.new`) for debugging, interviews, demos, and safe experiments. Boxes auto-destroy after N minutes, require no guest signup, and are auditable and policy-controlled.

## Key Features

- ✅ **Instant provisioning** (<= 3s to usable shell)
- ✅ **Strong security**: least-privilege, ephemeral credentials, per-session isolation
- ✅ **Auditable**: recorded session metadata, optional session recording
- ✅ **Resource control & limits**: quotas, time-to-live (TTL), CPU/memory caps
- ✅ **Simple UX**: single-step access for guests, integration with org SSO for staff
- ✅ **Cost control**: reuse base images, snapshot layering, and aggressive teardown

## Architecture

The sshBox system consists of several components:

```
[User] --> [SSH Gateway] --> [Provisioner] --> [Runtime (Container/VM)]
     |            |              |
     v            v              v
[Invite CLI] [Token Validation] [Session Recording]
```

### Components

1. **SSH Gateway** (FastAPI): Validates tokens and routes connections
2. **Provisioner**: Creates ephemeral runtimes (containers or Firecracker microVMs)
3. **Invite CLI**: Generates signed tokens and manages connections
4. **Session Recorder**: Records and audits SSH sessions
5. **Database**: Stores session metadata and audit logs

## Quick Start

### Prerequisites

- Docker
- Python 3.8+
- SSH client

### Setup

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd sshBoxes
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Build the base image:
   ```bash
   cd images
   docker build -t sshbox-base:latest -f Dockerfile .
   ```

4. Start the services:
   ```bash
   docker-compose up -d
   ```

### Creating an Invite

As a host, create an invite token:

```bash
# Create an invite for a dev box lasting 30 minutes
./scripts/box-invite.py create --secret "your-secret-key" --profile dev --ttl 1800
```

### Connecting to a Box

As a guest, use the token to connect:

```bash
# Connect using the token you received
./scripts/box-invite.py connect --token "dev:1800:1234567890:abcd1234:none:somesignature" --gateway http://localhost:8080
```

## Runtime Options

The system supports both container and microVM runtimes:

### Container Runtime (Default)
- Fast startup (~1-2 seconds)
- Lower resource overhead
- Good for development and testing

### Firecracker MicroVM Runtime
- Stronger isolation
- Better security guarantees
- Slightly higher startup time (~3-5 seconds)
- Requires additional setup (see `docs/firecracker_implementation.md`)

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GATEWAY_SECRET` | Secret key for token validation | `replace-with-secret` |
| `GATEWAY_PORT` | Port for the gateway service | `8080` |
| `DB_HOST` | Database host | `localhost` |
| `DB_NAME` | Database name | `sshbox` |
| `DB_USER` | Database user | `sshbox_user` |
| `DB_PASS` | Database password | `sshbox_pass` |

### Profiles

The system supports different profiles with varying capabilities:

- `dev`: Standard development environment with internet access
- `debug`: Includes cloud CLIs and debugging tools
- `secure-shell`: Isolated environment with no network access
- `privileged`: High-privilege environment for staff (approval required)

See `schemas/profile_schema.yaml` for detailed configuration options.

## Security Model

- **Ephemeral keys**: Keys generated client-side and never stored server-side
- **Network isolation**: Default deny egress with configurable allowlists
- **Least privilege**: Per-profile capability restrictions
- **Automatic cleanup**: Resources destroyed after TTL expiration
- **Session recording**: Optional recording of all SSH sessions

## Data Schemas

The system uses standardized schemas for different data types:

- **Invite Token Format**: `profile:ttl:timestamp:recipient_hash:notes_hash:signature`
- **Session Metadata**: See `schemas/session_metadata_schema.json`
- **Profile Configuration**: See `schemas/profile_schema.yaml`

## Development

### Running Tests

```bash
# Run unit tests
python -m unittest discover tests/ -v

# Run API tests
python -m pytest tests/test_api.py -v
```

### Project Structure

```
sshBoxes/
├── api/                    # API implementations (FastAPI)
├── scripts/               # Shell scripts for provisioning
├── images/                # Dockerfiles
├── schemas/               # Data schemas
├── docs/                  # Documentation
├── tests/                 # Test suites
├── docker-compose.yml     # Docker orchestration
└── requirements.txt       # Dependencies
```

## Production Deployment

For production deployments, consider:

1. Using HTTPS with TLS termination
2. Implementing proper secrets management (HashiCorp Vault, AWS Secrets Manager)
3. Setting up monitoring and alerting
4. Configuring backup strategies for audit logs
5. Implementing rate limiting and abuse prevention

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Submit a pull request

## License

MIT License - see the LICENSE file for details.