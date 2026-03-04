#!/usr/bin/env python3
"""
Open Policy Agent (OPA) integration for sshBox
Provides policy-based access control for sessions, commands, and network access
"""
import os
import json
import logging
import hashlib
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from datetime import datetime
import requests
from dataclasses import dataclass, asdict
import threading

from api.exceptions import PolicyDeniedError, ConfigurationError
from api.logging_config import setup_logging
from api.circuit_breaker import CircuitBreaker, external_api_breaker

logger = setup_logging("policy_engine")


@dataclass
class PolicyInput:
    """Standard policy input structure"""
    user: Dict[str, Any]
    request: Dict[str, Any]
    resource: Dict[str, Any]
    context: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PolicyResult:
    """Policy evaluation result"""
    allowed: bool
    reason: str
    conditions: List[Dict[str, Any]] = None
    obligations: List[Dict[str, Any]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "conditions": self.conditions or [],
            "obligations": self.obligations or []
        }


class PolicyEngine:
    """
    OPA-based policy engine for sshBox
    
    Usage:
        engine = PolicyEngine(opa_url="http://localhost:8181")
        
        # Check if session creation is allowed
        result = engine.check_session_creation(
            user_id="user@example.com",
            profile="dev",
            ttl=1800,
            source_ip="192.168.1.1"
        )
        
        if not result.allowed:
            raise PolicyDeniedError(result.reason)
    """
    
    # Default Rego policies
    DEFAULT_POLICIES = {
        "session_authz": """
package sshbox.authz.session

import rego.v1

# Default deny
default create := false

# Allow dev profile for all users during business hours (8am-6pm UTC)
create if {
    input.request.profile == "dev"
    input.request.ttl <= 3600
    is_business_hours
    not is_blocked_user
}

# Allow debug profile only for staff and admins
create if {
    input.request.profile == "debug"
    user_is_staff_or_admin
    input.request.ttl <= 7200
}

# Allow secure-shell for all users (shorter TTL)
create if {
    input.request.profile == "secure-shell"
    input.request.ttl <= 1800
}

# Allow privileged profile only for admins with MFA
create if {
    input.request.profile == "privileged"
    user_is_admin
    input.user.mfa_verified
    input.request.ttl <= 3600
    source_ip_is_trusted
}

# Helper rules
is_business_hours if {
    hour := time.now_ns() / 1000000000 / 3600
    hour := (hour + 12) % 24  # Convert to UTC
    hour >= 8
    hour < 18
}

is_blocked_user if {
    some blocked_user in input.context.blocked_users
    blocked_user == input.user.id
}

user_is_staff_or_admin if {
    input.user.role == "staff"
    or input.user.role == "admin"
}

user_is_admin if {
    input.user.role == "admin"
}

source_ip_is_trusted if {
    some trusted in input.context.trusted_ips
    startswith(input.request.source_ip, trusted)
}

# Rate limiting check
default rate_limit_ok := true
rate_limit_exceeded if {
    input.context.request_count > input.context.rate_limit
}
""",
        
        "command_authz": """
package sshbox.authz.command

import rego.v1

# Default allow for most commands
default allow := true

# Deny dangerous commands
allow := false if {
    some pattern in input.context.dangerous_patterns
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
]

# Profile-specific command restrictions
allow := false if {
    input.profile == "secure-shell"
    some cmd in input.context.network_commands
    startswith(input.command, cmd)
}

network_commands := ["curl", "wget", "ping", "nc", "netcat", "ssh", "scp"]
""",
        
        "network_policy": """
package sshbox.authz.network

import rego.v1

# Default deny egress
default egress_allow := false

# Allow egress for dev profile
egress_allow if {
    input.profile == "dev"
    is_allowed_destination
}

# Allow specific destinations for debug profile
egress_allow if {
    input.profile == "debug"
    some dest in input.context.allowed_debug_destinations
    dest == input.destination
}

# No egress for secure-shell
egress_allow if {
    input.profile == "secure-shell"
    false  # Always deny
}

# Helper rules
is_allowed_destination if {
    # Allow common package registries and code hosting
    some allowed in input.context.allowed_destinations
    allowed == input.destination
}

allowed_destinations := [
    "github.com:443",
    "pypi.org:443",
    "registry.npmjs.org:443",
    "repo.maven.apache.org:443",
    "golang.org:443",
    "rubygems.org:443",
]

allowed_debug_destinations := [
    "kubernetes.default.svc.cluster.local:443",
    "*.amazonaws.com:443",
    "*.googleapis.com:443",
    "*.azure.com:443",
]
""",
        
        "risk_assessment": """
package sshbox.risk

import rego.v1

# Calculate risk score (0-100)
default risk_score := 0
default risk_level := "low"

# Base risk factors
risk_score := score if {
    score := sum([
        time_risk,
        user_risk,
        profile_risk,
        location_risk,
    ])
}

time_risk := 20 if {
    hour := (time.now_ns() / 1000000000 / 3600) % 24
    hour < 6 or hour > 22  # Outside business hours
} else := 0

user_risk := 30 if {
    input.user.role == "trial"
} else := 10 if {
    input.user.role == "default"
} else := 0

profile_risk := 40 if {
    input.request.profile == "privileged"
} else := 20 if {
    input.request.profile == "debug"
} else := 10

location_risk := 25 if {
    not input.context.known_location
} else := 0

# Risk level classification
risk_level := "critical" if { risk_score >= 80 }
else := "high" if { risk_score >= 60 }
else := "medium" if { risk_score >= 40 }
else := "low"

# Require additional approval for high risk
default requires_approval := false
requires_approval if {
    risk_level == "high"
    or risk_level == "critical"
}

# Require MFA for elevated risk
default requires_mfa := false
requires_mfa if {
    risk_score >= 30
}
"""
    }
    
    def __init__(
        self,
        opa_url: str = None,
        policy_package: str = "sshbox/authz",
        policies_dir: str = "/etc/sshbox/policies",
        enable_local_fallback: bool = True,
        timeout_seconds: int = 5
    ):
        """
        Initialize policy engine
        
        Args:
            opa_url: URL of OPA server (e.g., http://localhost:8181)
            policy_package: Base policy package name
            policies_dir: Directory for local policy storage
            enable_local_fallback: Use local policy evaluation if OPA unavailable
            timeout_seconds: Request timeout
        """
        self.opa_url = opa_url or os.environ.get('OPA_URL', 'http://localhost:8181')
        self.policy_package = policy_package
        self.policies_dir = Path(policies_dir)
        self.enable_local_fallback = enable_local_fallback
        self.timeout_seconds = timeout_seconds
        
        # Create policies directory
        try:
            self.policies_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"Could not create policies directory: {e}")
            self.policies_dir = Path("/tmp/sshbox/policies")
            self.policies_dir.mkdir(parents=True, exist_ok=True)
        
        # Circuit breaker for OPA calls
        self.opa_breaker = CircuitBreaker(
            name="opa",
            failure_threshold=3,
            recovery_timeout=30
        )
        
        # Local policy cache
        self._policy_cache: Dict[str, str] = {}
        self._cache_lock = threading.Lock()
        
        # Check OPA availability
        self.opa_available = self._check_opa_health()
        
        if self.opa_available:
            logger.info(f"OPA server available at {self.opa_url}")
            self._load_default_policies()
        elif enable_local_fallback:
            logger.warning("OPA unavailable, using local policy evaluation")
            self._load_local_policies()
        else:
            logger.error("OPA unavailable and no local fallback enabled")
    
    def _check_opa_health(self) -> bool:
        """Check if OPA server is healthy"""
        try:
            response = requests.get(
                f"{self.opa_url}/health",
                timeout=self.timeout_seconds
            )
            return response.status_code == 200
        except Exception as e:
            logger.debug(f"OPA health check failed: {e}")
            return False
    
    def _load_default_policies(self):
        """Load default policies into OPA"""
        for policy_name, policy_content in self.DEFAULT_POLICIES.items():
            self.load_policy(policy_name, policy_content)
    
    def _load_local_policies(self):
        """Load policies from local storage"""
        for policy_name, policy_content in self.DEFAULT_POLICIES.items():
            policy_path = self.policies_dir / f"{policy_name}.rego"
            if policy_path.exists():
                with open(policy_path, 'r') as f:
                    self._policy_cache[policy_name] = f.read()
            else:
                with open(policy_path, 'w') as f:
                    f.write(policy_content)
                self._policy_cache[policy_name] = policy_content
    
    def load_policy(self, policy_name: str, policy_content: str) -> bool:
        """
        Load a policy into OPA
        
        Args:
            policy_name: Name of the policy
            policy_content: Rego policy content
        
        Returns:
            True if successful
        """
        try:
            # Save to local storage
            policy_path = self.policies_dir / f"{policy_name}.rego"
            with open(policy_path, 'w') as f:
                f.write(policy_content)
            
            # Update cache
            with self._cache_lock:
                self._policy_cache[policy_name] = policy_content
            
            # Upload to OPA if available
            if self.opa_available:
                response = requests.put(
                    f"{self.opa_url}/v1/policies/{policy_name}",
                    data=policy_content,
                    timeout=self.timeout_seconds
                )
                
                if response.status_code == 200:
                    logger.info(f"Policy {policy_name} loaded to OPA successfully")
                    return True
                else:
                    logger.error(f"Failed to load policy {policy_name} to OPA: {response.text}")
                    return False
            else:
                logger.info(f"Policy {policy_name} saved locally")
                return True
                
        except Exception as e:
            logger.error(f"Error loading policy {policy_name}: {e}")
            return False
    
    def evaluate(
        self,
        input_data: Dict[str, Any],
        policy_path: str = "session/create"
    ) -> PolicyResult:
        """
        Evaluate a policy decision
        
        Args:
            input_data: Input data for policy evaluation
            policy_path: Path to policy decision (e.g., "session/create")
        
        Returns:
            PolicyResult with decision and metadata
        """
        # Try OPA first
        if self.opa_available:
            try:
                return self._evaluate_opa(input_data, policy_path)
            except Exception as e:
                logger.warning(f"OPA evaluation failed: {e}, trying local fallback")
                self.opa_available = False
        
        # Fallback to local evaluation
        if self.enable_local_fallback:
            return self._evaluate_local(input_data, policy_path)
        
        # Default deny if no fallback
        return PolicyResult(
            allowed=False,
            reason="Policy engine unavailable"
        )
    
    def _evaluate_opa(
        self,
        input_data: Dict[str, Any],
        policy_path: str
    ) -> PolicyResult:
        """Evaluate policy using OPA server"""
        def opa_call():
            response = requests.post(
                f"{self.opa_url}/v1/data/{self.policy_package}/{policy_path}",
                json={"input": input_data},
                timeout=self.timeout_seconds
            )
            response.raise_for_status()
            return response.json()
        
        result = self.opa_breaker.call(opa_call)
        
        # Parse OPA result
        decision = result.get("result", {})
        
        if isinstance(decision, bool):
            return PolicyResult(
                allowed=decision,
                reason="Policy decision" if decision else "Policy denied"
            )
        
        return PolicyResult(
            allowed=decision.get("allowed", False),
            reason=decision.get("reason", "Policy decision"),
            conditions=decision.get("conditions", []),
            obligations=decision.get("obligations", [])
        )
    
    def _evaluate_local(
        self,
        input_data: Dict[str, Any],
        policy_path: str
    ) -> PolicyResult:
        """
        Evaluate policy using simple local rules (fallback)
        
        This is a simplified implementation for when OPA is unavailable.
        In production, always use OPA for policy evaluation.
        """
        # Simple rule-based fallback
        profile = input_data.get("request", {}).get("profile", "dev")
        ttl = input_data.get("request", {}).get("ttl", 1800)
        user_role = input_data.get("user", {}).get("role", "default")
        
        # Default deny for privileged profiles
        if profile == "privileged" and user_role not in ("admin", "staff"):
            return PolicyResult(
                allowed=False,
                reason="Privileged profile requires admin or staff role"
            )
        
        # TTL limits by profile
        max_ttls = {
            "dev": 3600,
            "debug": 7200,
            "secure-shell": 1800,
            "privileged": 3600
        }
        
        if ttl > max_ttls.get(profile, 3600):
            return PolicyResult(
                allowed=False,
                reason=f"TTL {ttl}s exceeds maximum for {profile} profile"
            )
        
        # Default allow for other cases
        return PolicyResult(
            allowed=True,
            reason="Policy allowed (local fallback)"
        )
    
    def check_session_creation(
        self,
        user_id: str,
        profile: str,
        ttl: int,
        source_ip: str,
        user_role: str = "default",
        mfa_verified: bool = False,
        additional_context: Dict = None
    ) -> PolicyResult:
        """
        Check if session creation is allowed by policy
        
        Args:
            user_id: User identifier
            profile: Requested profile
            ttl: Requested TTL in seconds
            source_ip: Source IP address
            user_role: User role
            mfa_verified: Whether MFA is verified
            additional_context: Additional context data
        
        Returns:
            PolicyResult with decision
        """
        # Build policy input
        input_data = {
            "user": {
                "id": user_id,
                "role": user_role,
                "mfa_verified": mfa_verified
            },
            "request": {
                "action": "create_session",
                "profile": profile,
                "ttl": ttl,
                "source_ip": source_ip
            },
            "resource": {
                "type": "ssh_box",
                "profile": profile
            },
            "context": {
                "time_of_day": datetime.utcnow().strftime("%H:%M"),
                "day_of_week": datetime.utcnow().strftime("%A"),
                "blocked_users": [],  # Could be fetched from database
                "trusted_ips": ["10.", "192.168.", "172.16."],
                "request_count": 0,  # Could be fetched from rate limiter
                "rate_limit": 10,
                **(additional_context or {})
            }
        }
        
        result = self.evaluate(input_data, "session/create")
        
        # Log decision
        if result.allowed:
            logger.info(
                f"Session creation allowed: user={user_id}, profile={profile}",
                extra={"user_id": user_id, "profile": profile}
            )
        else:
            logger.warning(
                f"Session creation denied: user={user_id}, profile={profile}, reason={result.reason}",
                extra={"user_id": user_id, "profile": profile}
            )
        
        return result
    
    def check_command_execution(
        self,
        session_id: str,
        user_id: str,
        command: str,
        profile: str
    ) -> PolicyResult:
        """
        Check if command execution is allowed by policy
        
        Args:
            session_id: Session identifier
            user_id: User identifier
            command: Command to execute
            profile: Session profile
        
        Returns:
            PolicyResult with decision
        """
        input_data = {
            "user": {"id": user_id},
            "session": {"id": session_id, "profile": profile},
            "command": command,
            "context": {
                "dangerous_patterns": [
                    "rm -rf /",
                    "mkfs",
                    "dd if=/dev/zero",
                ],
                "network_commands": ["curl", "wget", "ping", "nc", "ssh"]
            }
        }
        
        return self.evaluate(input_data, "command/allow")
    
    def check_network_access(
        self,
        session_id: str,
        profile: str,
        destination: str,
        port: int
    ) -> PolicyResult:
        """
        Check if network access is allowed by policy
        
        Args:
            session_id: Session identifier
            profile: Session profile
            destination: Destination hostname/IP
            port: Destination port
        
        Returns:
            PolicyResult with decision
        """
        input_data = {
            "session": {"id": session_id, "profile": profile},
            "destination": f"{destination}:{port}",
            "context": {
                "allowed_destinations": [
                    "github.com:443",
                    "pypi.org:443",
                    "registry.npmjs.org:443",
                ],
                "allowed_debug_destinations": [
                    "*.amazonaws.com:443",
                    "*.googleapis.com:443",
                ]
            }
        }
        
        return self.evaluate(input_data, "network/egress")
    
    def assess_risk(
        self,
        user_id: str,
        profile: str,
        ttl: int,
        source_ip: str,
        user_role: str = "default"
    ) -> Dict[str, Any]:
        """
        Assess risk level for a session request
        
        Args:
            user_id: User identifier
            profile: Requested profile
            ttl: Requested TTL
            source_ip: Source IP
            user_role: User role
        
        Returns:
            Risk assessment with score and level
        """
        input_data = {
            "user": {"id": user_id, "role": user_role},
            "request": {"profile": profile, "ttl": ttl},
            "context": {
                "known_location": source_ip.startswith(("10.", "192.168.", "172."))
            }
        }
        
        result = self.evaluate(input_data, "risk/score")
        
        return {
            "user_id": user_id,
            "risk_score": result.conditions.get("score", 0) if result.conditions else 0,
            "risk_level": result.obligations.get("level", "unknown") if result.obligations else "unknown",
            "requires_approval": result.allowed and result.conditions.get("requires_approval", False) if result.conditions else False,
            "requires_mfa": result.obligations.get("requires_mfa", False) if result.obligations else False
        }
    
    def get_policy_status(self) -> Dict[str, Any]:
        """Get policy engine status"""
        return {
            "opa_available": self.opa_available,
            "opa_url": self.opa_url,
            "fallback_enabled": self.enable_local_fallback,
            "policies_loaded": list(self._policy_cache.keys()),
            "circuit_breaker_state": self.opa_breaker.state.value
        }


# Global policy engine instance
_policy_engine: Optional[PolicyEngine] = None


def get_policy_engine() -> PolicyEngine:
    """Get or create global policy engine instance"""
    global _policy_engine
    if _policy_engine is None:
        _policy_engine = PolicyEngine(
            opa_url=os.environ.get('OPA_URL', 'http://localhost:8181'),
            enable_local_fallback=os.environ.get('POLICY_FALLBACK', 'true').lower() == 'true'
        )
    return _policy_engine


def reload_policy_engine() -> PolicyEngine:
    """Reload policy engine configuration"""
    global _policy_engine
    _policy_engine = None
    return get_policy_engine()
