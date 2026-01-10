# sshBox

## Ephemeral SSH Boxes — Expanded Design & Implementation Plan

Goal: Provide instantly-provisioned, secure, short-lived Linux bastions ("ephemeral boxes") accessible via a single SSH command (e.g., `ssh box.new`) for debugging, interviews, demos, and safe experiments. Boxes auto-destroy after N minutes, require no guest signup, and are auditable and policy-controlled.

Key design principles

- Instant provisioning (<= 3s to usable shell).
- Strong security: least-privilege, ephemeral credentials, per-session isolation.
- Auditable: recorded session metadata, optional session recording.
- Resource control & limits: quotas, time-to-live (TTL), CPU/memory caps.
- UX: single-step access for guests, integration with org SSO for staff.
- Cost control: reuse base images, snapshot layering, and aggressive teardown.

## High-level architecture

- Frontend DNS/CLI: a short domain (box.new) backed by an SSH gateway that routes sessions to ephemeral VMs/containers.
- Provisioner: creates ephemeral runtime (container or lightweight VM) from immutable image + init script.
- Broker/Master: maintains mapping of session → instance, enforces policy, issues ephemeral SSH keys.
- Telemetry & Audit Store: logs session start/stop, commands (optional), diagnostics, and billing tags.
- Secrets & Policies: Vault for secrets, policy engine for risk-based gating.
- Destroyer: TTL enforcer and on-demand cleanup with rollback/forensics snapshot.

## Options for runtime: container vs lightweight VM

- Containers (e.g., Firecracker microVMs via Kata, gVisor, or privileged Docker):
    - Pros: fast startup, low cost, resource-efficient.
    - Cons: weaker kernel isolation unless using microVMs.
- Lightweight microVMs (Firecracker, Cloud Hypervisor):
    - Pros: strong isolation, fast (hundreds of ms to seconds).
    - Cons: slightly more complex infra.
    Recommendation: Use Firecracker microVMs (or AWS Nitro Enclaves equivalent) for secure isolation with near-container startup speed. For ultra-fast internal-only use, sandboxed containers with strong syscall filtering are acceptable.

## UX and access flow

1. User runs `ssh box.new` (or a web link) — DNS maps to SSH gateway IP.
2. SSH gateway authenticates client:
    - Guest flow: generate ephemeral keypair client-side, or gateway issues a one-time challenge key; no signup required.
    - Authenticated staff: SSO integration (OIDC/SAML) and short-lived certs.
3. Gateway requests a new runtime from Provisioner, passing requested profile (image, TTL, capabilities).
4. Provisioner creates runtime, injects ephemeral public key or enables SSHd with temporary credentials, and returns target host:port.
5. Gateway proxies the SSH connection to the runtime.
6. Session records metadata (user id/anon token, TTL, allowed actions, start time).
7. On TTL expiry or session end, Destroyer tears down runtime; optional snapshot saved for audit.

## Authentication & guest/no-signup model

- Guests receive ephemeral tokens via:
    - Short-lived link (e.g., emailed or created on-the-fly by host).
    - One-time password delivered out-of-band.
    - Browser-based key generation with WebAuthn fallback.
- Implementation approach (no signup):
    - Host creates an invite token tied to scope and TTL; token encodes role & capabilities and signed by CA.
    - Guest runs `ssh -i <ephemeral_key> box.new` where key was obtained via the invite link or `ssh box.new?token=XYZ` helper CLI that performs token exchange and generates ephemeral key pair locally.

Security model

- Staff flow: certificate-based authentication via SSH CA (short-lived certs minted after SSO).
- Per-session ephemeral SSH keys or certs; private keys generated client-side and never stored server-side.
- Network isolation: default deny egress; allowlist only required endpoints (or provide outbound NAT via egress proxy).
- Mounting and persistence: runtime starts with ephemeral filesystem; explicit attach of read-only data volumes if needed.
- Least-privilege container/VM profiles (capabilities, seccomp, AppArmor).
- Secrets handling: no secrets in images; secrets provided via ephemeral secret injection with short TTL and audit.
- Mandatory session recording for guests in sensitive environments (configurable).
- Auto-destroy and emergency kill-switch.

Profiles and capabilities

- Predefined profiles selectable on request:
    - dev: SSH + basic dev tools, internet egress allowed, 1 vCPU, 2GB RAM, TTL default 30m.
    - debug: includes kubectl, cloud CLIs, limited IAM role for read-only observability, TTL 60m.
    - secure-shell: no network egress, fully isolated, TTL 10m (for untrusted guests).
    - privileged (staff only): temporary escalations (requires approval), tighter audit.
- Custom profiles via host-generated invites specifying capabilities and TTL.

Provisioning details

- Use immutable golden images with layered overlays:
    - Base image (minimal OS).
    - Profile overlay (tools, language runtimes).
    - On-the-fly bootstrap script (user dotfiles, authorized_keys injection).
- Store images in fast registry (OCI for microVM or container images).
- Utilize cached warm pool of paused microVMs per profile to reach near-instant start times.
- Warm pool sizing: autoscaled based on minutes-of-day traffic patterns.

Session routing & networking

- SSH gateway runs a high-performance proxy (e.g., h2-proxy or custom multiplexor) mapping incoming sessions to ephemeral endpoints.
- Assign internal IPs from ephemeral pool and NAT to internet via egress gateways (policy enforced).
- For cluster debugging, offer option to "attach" box to a service network (requires approval) with network policies.

Audit, recording, and replay

- Capture metadata: invoker token (anon or user), profile, start/end, target container/VM id, commands invoked (PTY/full recording).
- Optional full session recording via asciinema-like recorder or server-side `script` capturing stdout/stderr and timestamps.
- Retention controls and encryption of recordings; access limited by role and audit.
- Provide replay UI for reviewers (time-synced playback).

Governance & policy controls

- Policy engine enforces:
    - TTL limits per user or invite.
    - Allowed profiles per actor.
    - Egress allowlist/denylist.
    - Blackout windows (no access during maintenance).
- Integration with org IAM for staff; invite-based scopes for guests.
- Approval workflows for privileged profile requests; can be automated via webhook to Slack/Teams.

Cost, scaling, and cleanup

- Aggressive teardown: destroy immediately on TTL expiry; reclaim resources within seconds.
- Warm pool to reduce cold-start costs; size managed by autoscaler.
- Per-invite billing tags for cost tracking; quotas per team.
- Idle detection to terminate early (no active SSH I/O for X minutes).
- Reuse ephemeral storage layers across sessions to reduce image pull time.

Developer & operator tooling

- CLI helpers:
    - `box.new --profile debug --ttl 60m` -> returns SSH target or auto-connects.
    - `box.invite --profile debug --ttl 30m --email alice@example.com` -> creates one-time invite link.
    - `box.status <session-id>` -> show metadata, actions, snapshot.
- Web portal:
    - Create invites, choose profile, preview session policies, view active sessions and recordings.
- GitOps for profiles and images stored in repo; PR-based changes.

Implementation stack suggestions

- Provisioner: custom service in Go/Python. Use libvirt or Firecracker SDK for microVMs.
- Image format: OCI images for microVM rootfs (cage FS) or VM images on S3.
- SSH Gateway: OpenSSH with ProxyCommand plugin or custom Go proxy using ssh library for auth+proxy.
- Secret management: HashiCorp Vault or cloud KMS.
- Policy engine: OPA (Open Policy Agent) or simple rules in service.
- Telemetry: Prometheus metrics and ELK/ClickHouse for logs/recordings metadata.
- Orchestration: Kubernetes for control plane services; use serverless/autoscaling groups for provisioner workers.
- CI: build image pipelines that produce signed artifacts.

Starter implementation plan (MVP)

1. Build simple flow with containers (fastest to prototype):
    - SSH gateway that accepts ephemeral tokens and forwards to a container runtime.
    - Provision containers from a prebuilt image, inject authorized_key, start sshd.
    - Destroy after TTL; log metadata to SQLite.
2. Add warm pool to reduce latency.
3. Add profiles, TTL enforcement, and simple audit recording (tty capture).
4. Harden with microVM engine (Firecracker) and Vault integration.
5. Add web/CLI invite UX and staff SSO.
6. Add policy engine (OPA), session replay UI, and canary testing.
7. Integrate with billing and quotas, and scale to production.

Security hardening checklist

- Ephemeral keys only; client generates keys locally.
- Network egress default-deny; whitelist required endpoints.
- No long-term secrets in images; secrets injected per-session and revoked.
- Image signing and verification before boot.
- Immutable images; no build-time secrets.
- Rate limits and anti-abuse (CAPTCHA or human approval for public invites).
- Regular rotation of CA keys and secrets.

Operational considerations

- Abuse handling: throttle or require identity for suspicious patterns.
- Legal/privacy: session recordings have privacy implications—consent and retention policies required.
- Failure modes: ensure destroyer can forcibly clean up orphaned resources.
- Observability: surfaced metrics for start latency, cost per session, average TTL, active sessions.

Example minimal guest flow (MVP)

1. Host runs `box.invite --profile debug --ttl 30m` and shares invite link.
2. Guest opens link, browser returns ephemeral token and downloads small CLI helper or runs `ssh -o ProxyCommand="box-proxy --token=XYZ" root@box.new`.
3. Gateway provisions container, injects public key, proxies SSH, session starts.
4. On disconnect or TTL expiry, container destroyed; metadata stored in audit DB.

Wrap-up
Ephemeral SSH Boxes provide high-value developer/ops UX when designed for speed, safety, and auditability. Start with a container-based MVP for rapid iteration, then evolve to microVMs for stronger isolation. Focus first on secure, minimal guest onboarding, TTL enforcement, audit recording, and warm pools to achieve near-instant availability.

- Provide the  data schemas (invite token format, session metadata record, profile YAML).
- create a PoC architecture a Docker-based  script sequence.
- Write an example `box.invite` CLI in Python that issues invites and provisions a container-based runtime.

 "ephemeral SSH box" using containers. It includes:

- [box-provision.sh](http://box-provision.sh/) — provisioner: creates a container, injects an ephemeral SSH key, starts sshd, returns connect info.
- [box-destroy.sh](http://box-destroy.sh/) — destroyer: stops and removes container.
- [box-invite.py](http://box-invite.py/) — simple invite CLI: generates a signed token (HMAC) and a client helper that exchanges token for an ephemeral key and SSH target.
- [ssh-gateway-proxy.sh](http://ssh-gateway-proxy.sh/) — simple SSH gateway helper that validates token and proxies to container via SSH (uses socat).
- Minimal instructions to run locally with Docker.

This prototype is intentionally simple (no Vault/CA/SSO). It demonstrates the end-to-end flow: host creates invite token; guest runs client helper which posts token to gateway; gateway provisions container and returns SSH details; guest connects; destroyer removes container after TTL.

Prereqs: Docker, Python 3, socat, curl, openssh-client/server installed.

1. [box-provision.sh](http://box-provision.sh/)

```bash
#!/usr/bin/env bash
# Usage: ./box-provision.sh <session_id> <pubkey> <profile> <ttl_seconds>
set -euo pipefail
SESSION_ID="$1"
PUBKEY="$2"
PROFILE="${3:-dev}"
TTL="${4:-1800}"  # default 30m
IMAGE="ephemeral-box:latest"  # ensure image exists with sshd installed
CONTAINER_NAME="box_${SESSION_ID}"
# Create container
docker run -d --name "$CONTAINER_NAME" -p 0:22 --rm "$IMAGE" sleep infinity
# get dynamically mapped ssh port
SSH_PORT=$(docker port "$CONTAINER_NAME" 22 | sed -E 's/.*:(.*)/\\1/')
# inject user and ssh key (assume user 'boxuser' exists in image)
docker exec "$CONTAINER_NAME" mkdir -p /home/boxuser/.ssh
docker exec -i "$CONTAINER_NAME" bash -lc "cat > /home/boxuser/.ssh/authorized_keys" <<< "$PUBKEY"
docker exec "$CONTAINER_NAME" chown -R boxuser:boxuser /home/boxuser/.ssh && chmod 700 /home/boxuser/.ssh && chmod 600 /home/boxuser/.ssh/authorized_keys
# record metadata
echo "{\\"session_id\\":\\"$SESSION_ID\\",\\"container\\":\\"$CONTAINER_NAME\\",\\"ssh_port\\":$SSH_PORT,\\"ttl\\":$TTL,\\"created_at\\":\\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\\"}" > "/tmp/${SESSION_ID}.json"
# schedule destroy
( sleep "$TTL"; ./box-destroy.sh "$CONTAINER_NAME" "/tmp/${SESSION_ID}.json" ) & disown
# output connect info
echo "{\\"host\\":\\"$(hostname -I | awk '{print $1}')\\",\\"port\\":$SSH_PORT,\\"user\\":\\"boxuser\\",\\"session_id\\":\\"$SESSION_ID\\"}"

```

1. [box-destroy.sh](http://box-destroy.sh/)

```bash
#!/usr/bin/env bash
# Usage: ./box-destroy.sh <container_name> <metadata_file_optional>
set -euo pipefail
CONTAINER="$1"
META="${2:-}"
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER}\\$"; then
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
fi
if [ -n "$META" ] && [ -f "$META" ]; then
  mv "$META" "${META}.destroyed"
fi
echo "Destroyed $CONTAINER"

```

1. [ssh-gateway-proxy.sh](http://ssh-gateway-proxy.sh/) (simple token exchange endpoint using netcat + small HTTP handling)

```bash
#!/usr/bin/env bash
# Minimal HTTP endpoint to accept token exchange: POST /request with JSON {"token":"...","pubkey":"...","profile":"dev","ttl":300}
# Returns JSON with host/port/user/session_id or error.
set -euo pipefail
PORT="${GATEWAY_PORT:-8080}"
SECRET="${GATEWAY_SECRET:-replace-with-secret}"  # HMAC secret for token validation
PROVISIONER="./box-provision.sh"

# very small loop using nc to accept one request at a time (for demo only)
while true; do
  # listen for one HTTP request
  req=$(nc -l -p "$PORT" -q 1)
  body=$(echo "$req" | sed -n '/^\\r$/,$p' | sed '1d')
  token=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
  pubkey=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin)['pubkey'])")
  profile=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('profile','dev'))")
  ttl=$(echo "$body" | python3 -c "import sys,json; print(int(json.load(sys.stdin).get('ttl',1800)))")
  # validate token (HMAC)
  valid=$(echo -n "$token" | python3 -c "import sys,hashlib,hmac; s='$SECRET'; t=sys.stdin.read().strip(); print('1' if hmac.compare_digest(hmac.new(s.encode(), t.encode(), hashlib.sha256).hexdigest(), t.split(':')[-1]) else '0')")
  if [ "$valid" != "1" ]; then
    echo -e "HTTP/1.1 403 Forbidden\\r\\nContent-Type: application/json\\r\\n\\r\\n{\\"error\\":\\"invalid token\\"}"
    continue
  fi
  SESSION_ID=$(date +%s%N)
  # call provisioner
  resp=$($PROVISIONER "$SESSION_ID" "$pubkey" "$profile" "$ttl")
  echo -e "HTTP/1.1 200 OK\\r\\nContent-Type: application/json\\r\\n\\r\\n$resp"
done

```

1. [box-invite.py](http://box-invite.py/) (host-side invite creator and client helper)

```python
#!/usr/bin/env python3
"""
Usage:
  Create invite: ./box-invite.py create --secret SECRET --profile dev --ttl 600
  Client helper: ./box-invite.py connect --token <TOKEN> --gateway <http://localhost:8080> --privkey-path ./id_box
"""
import argparse, hmac, hashlib, time, json, os, subprocess, tempfile, requests

def create_invite(secret, profile='dev', ttl=600):
    # token payload: profile:ttl:timestamp:signature
    ts=str(int(time.time()))
    payload=f"{profile}:{ttl}:{ts}"
    sig=hmac.new(secret.encode(),'{}'.format(payload).encode(),hashlib.sha256).hexdigest()
    token=f"{payload}:{sig}"
    print(token)
    return token

def client_connect(token, gateway, privkey_path=None):
    # generate keypair locally
    if privkey_path is None:
        privkey_path = "./id_box"
    pubkey_path = privkey_path + ".pub"
    subprocess.run(["ssh-keygen","-t","ed25519","-f",privkey_path,"-N",""], check=True)
    with open(pubkey_path,'r') as f: pubkey=f.read().strip()
    # POST to gateway
    resp = requests.post(gateway+"/request", json={"token": token, "pubkey": pubkey, "profile":"dev", "ttl":300})
    if resp.status_code!=200:
        print("Gateway error:", resp.text); return
    info=resp.json()
    host=info['host']; port=info['port']; user=info['user']
    # connect via ssh
    ssh_cmd=["ssh","-i",privkey_path,f"{user}@{host}","-p",str(port)]
    print("Connecting:", " ".join(ssh_cmd))
    os.execvp("ssh", ssh_cmd)

if __name__ == "__main__":
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest='cmd')
    c=sub.add_parser('create'); c.add_argument('--secret',required=True); c.add_argument('--profile',default='dev'); c.add_argument('--ttl',type=int,default=600)
    k=sub.add_parser('connect'); k.add_argument('--token',required=True); k.add_argument('--gateway',default='<http://localhost:8080>'); k.add_argument('--privkey-path',default='./id_box')
    args=p.parse_args()
    if args.cmd=='create':
        create_invite(args.secret, args.profile, args.ttl)
    elif args.cmd=='connect':
        client_connect(args.token, args.gateway, args.privkey_path)

```

1. Build minimal Docker image (Dockerfile)

```
FROM ubuntu:22.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server sudo && mkdir /var/run/sshd
# add user boxuser with no password
RUN useradd -m -s /bin/bash boxuser && echo "boxuser ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/boxuser
# allow ssh login
RUN sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin no/' /etc/ssh/sshd_config
EXPOSE 22
CMD ["/usr/sbin/sshd","-D"]

```

1. Run instructions (local demo)
- Build image:
docker build -t ephemeral-box:latest .
- Start gateway:
export GATEWAY_SECRET="mysecret"
./ssh-gateway-proxy.sh & # defaults to port 8080
- Create invite token (host):
./box-invite.py create --secret mysecret --profile dev --ttl 600
This prints a token string.
- Guest connect:
./box-invite.py connect --token "<token>" --gateway "[http://localhost:8080](http://localhost:8080/)"
The script will generate a local keypair and attempt to connect via SSH to the provisioned container.
- Container will be auto-destroyed after TTL (handled by provisioner scheduling).

Notes, limitations, and next steps

- This prototype is intentionally minimal and synchronous (gateway loop uses nc). Replace with a proper HTTP server (Flask/FastAPI/Go) in real use.
- HMAC token scheme here is demo-only; use signed JWTs or CA-signed invites for production.
- The provisioner uses Docker host SSH port mapping; in real infra use microVMs, VPC-private IPs, SSH proxying via SSH multiplex or bastion.
- Session recording, audit logs, network policies, secrets injection, rate limits, and approval flows are omitted for brevity—add as next iterations.
- Replace socat/nc with an SSH proxy (ssh multiplex or ProxyCommand) or a proper SSH gateway service.

- Convert the gateway to a FastAPI service with endpoints and proper token validation.
- Add session recording (tty rec) and metadata storage in SQLite.
- Swap Docker for Firecracker microVM provisioning steps.

 both useful and sufficiently novel for many teams.

Why?

- Solves a real pain: fast, low-friction access for guests and quick-debug workflows without long-lived credentials or manual VM setup.
- Combines existing building blocks in a productizable way: ephemeral credentials, warm pools, microVMs, policy gating, and audit/recording — that combination with guest/no-signup UX is high-value.
- Differentiators vs. existing tools: instant invite flow + auto-destroy + fine-grained policy/TTL + session recording + canary execution make it safer and more developer-friendly than ad-hoc jump hosts or permanent bastions.
- Low technical risk for MVP (container prototype), high operational upside if hardened (Firecracker, Vault, OPA).

 prioritize:

- High-value if you frequently run external interviews, contractor access, customer troubleshooting, or on-call war-rooms.
- Start as an internal MVP (containers + invite links + recordings) and iterate toward microVM isolation and policy automation.
