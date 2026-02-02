# sshBox - Ephemeral SSH Boxes

Ephemeral SSH Boxes provide instantly-provisioned, secure, short-lived Linux bastions ("ephemeral boxes") accessible via a single SSH command (e.g., `ssh box.new`) for debugging, interviews, demos, and safe experiments. Boxes auto-destroy after N minutes, require no guest signup, and are auditable and policy-controlled.

## Architecture

- **Frontend DNS/CLI**: A short domain (box.new) backed by an SSH gateway that routes sessions to ephemeral VMs/containers.
- **Provisioner**: Creates ephemeral runtime (container or lightweight VM) from immutable image + init script.
- **Broker/Master**: Maintains mapping of session → instance, enforces policy, issues ephemeral SSH keys.
- **Telemetry & Audit Store**: Logs session start/stop, commands (optional), diagnostics, and billing tags.
- **Secrets & Policies**: Vault for secrets, policy engine for risk-based gating.
- **Destroyer**: TTL enforcer and on-demand cleanup with rollback/forensics snapshot.

## Prerequisites

- Docker
- Python 3
- socat
- curl
- openssh-client/server

## Project Structure

```
sshbox/
├── images/
│   └── Dockerfile          # Base image for ephemeral boxes
├── scripts/
│   ├── box-provision.sh    # Provisioner: creates container, injects SSH key
│   ├── box-destroy.sh      # Destroyer: stops and removes container
│   ├── ssh-gateway-proxy.sh # SSH gateway helper
│   └── box-invite.py       # Invite CLI: generates signed tokens
└── docs/
    └── README.md           # This file
```

## Quick Start

1. Build the base image:
   ```bash
   docker build -t ephemeral-box:latest images/
   ```

2. Start the gateway:
   ```bash
   export GATEWAY_SECRET="mysecret"
   ./scripts/ssh-gateway-proxy.sh & # defaults to port 8080
   ```

3. Create an invite token (host):
   ```bash
   ./scripts/box-invite.py create --secret mysecret --profile dev --ttl 600
   ```
   This prints a token string.

4. Guest connect:
   ```bash
   ./scripts/box-invite.py connect --token "<token>" --gateway "http://localhost:8080"
   ```
   The script will generate a local keypair and attempt to connect via SSH to the provisioned container.

5. The container will be auto-destroyed after TTL (handled by provisioner scheduling).

## Scripts

### box-provision.sh
Provisions a container with the given session ID, public key, profile, and TTL.

### box-destroy.sh
Destroys the specified container and cleans up metadata.

### ssh-gateway-proxy.sh
Simple SSH gateway helper that validates token and proxies to container via SSH.

### box-invite.py
Invite CLI that generates signed tokens and provides client connection helper.

## Security Features

- Ephemeral keys only; client generates keys locally
- Network egress default-deny; whitelist required endpoints
- No long-term secrets in images; secrets injected per-session and revoked
- Image signing and verification before boot
- Immutable images; no build-time secrets
- Rate limits and anti-abuse measures

## Next Steps

1. Improve the gateway to use a proper HTTP server instead of netcat
2. Add session recording functionality
3. Implement policy engine (OPA) integration
4. Add web portal for invite management
5. Integrate with billing and quotas