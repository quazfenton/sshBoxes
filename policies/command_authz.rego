package sshbox.authz.command

import rego.v1

# Default allow for most commands
default allow := true

# Deny dangerous commands
allow := false if {
    some pattern in dangerous_patterns
    startswith(input.command, pattern)
}

# Dangerous command patterns
dangerous_patterns := [
    "rm -rf /",
    "mkfs",
    "dd if=/dev/zero",
    ":(){:|:&}",
    "chmod -R 777 /",
    "chown -R root:root /",
    "wget -O- http",
    "curl http",
]

# Profile-specific command restrictions
allow := false if {
    input.profile == "secure-shell"
    some cmd in network_commands
    startswith(input.command, cmd)
}

network_commands := ["curl", "wget", "ping", "nc", "netcat", "ssh", "scp", "rsync"]
