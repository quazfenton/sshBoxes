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
    hour := (time.now_ns() / 1000000000 / 3600) % 24
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
