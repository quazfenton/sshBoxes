-- Database initialization script for sshBox
-- Comprehensive schema with support for sessions, recordings, quotas, audit logs, and metrics

-- ============================================================================
-- Enable extensions
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- For text search
CREATE EXTENSION IF NOT EXISTS "btree_gin";  -- For composite indexes

-- ============================================================================
-- Sessions Table
-- ============================================================================
CREATE TABLE IF NOT EXISTS sessions (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) UNIQUE NOT NULL,
    container_name VARCHAR(255) NOT NULL,
    ssh_host VARCHAR(255),
    ssh_port INTEGER,
    ssh_user VARCHAR(255) DEFAULT 'boxuser',
    profile VARCHAR(50) DEFAULT 'dev',
    ttl INTEGER DEFAULT 1800,
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP WITH TIME ZONE,
    ended_at TIMESTAMP WITH TIME ZONE,
    expires_at TIMESTAMP WITH TIME ZONE,
    user_id VARCHAR(255),
    invited_by VARCHAR(255),
    allowed_actions TEXT[],
    metadata JSONB DEFAULT '{}',
    recording_enabled BOOLEAN DEFAULT true,
    recording_path VARCHAR(500),
    source_ip INET,
    user_agent TEXT,
    destroy_reason VARCHAR(255),
    error_message TEXT,
    
    -- Constraints
    CONSTRAINT check_ttl CHECK (ttl > 0 AND ttl <= 28800),
    CONSTRAINT check_profile CHECK (profile IN ('dev', 'debug', 'secure-shell', 'privileged')),
    CONSTRAINT check_status CHECK (status IN ('active', 'pending', 'destroyed', 'ended', 'error', 'timeout'))
);

-- Indexes for sessions
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_profile ON sessions(profile);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_sessions_container ON sessions(container_name);
CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id);

-- GIN index for metadata JSONB
CREATE INDEX IF NOT EXISTS idx_sessions_metadata ON sessions USING GIN (metadata);

-- ============================================================================
-- Session Recordings Table
-- ============================================================================
CREATE TABLE IF NOT EXISTS session_recordings (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) REFERENCES sessions(session_id) ON DELETE CASCADE,
    recording_path VARCHAR(500) NOT NULL,
    recording_format VARCHAR(50) DEFAULT 'asciicast',
    recording_size BIGINT DEFAULT 0,
    duration_seconds INTEGER,
    frames_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    uploaded_to_s3 BOOLEAN DEFAULT false,
    s3_path VARCHAR(500),
    compression VARCHAR(50) DEFAULT 'none',
    checksum VARCHAR(64),
    
    CONSTRAINT check_format CHECK (recording_format IN ('asciicast', 'typescript', 'mp4'))
);

CREATE INDEX IF NOT EXISTS idx_recordings_session ON session_recordings(session_id);
CREATE INDEX IF NOT EXISTS idx_recordings_created ON session_recordings(created_at DESC);

-- ============================================================================
-- Invites Table
-- ============================================================================
CREATE TABLE IF NOT EXISTS invites (
    id SERIAL PRIMARY KEY,
    token VARCHAR(500) UNIQUE NOT NULL,
    profile VARCHAR(50) DEFAULT 'dev',
    ttl INTEGER DEFAULT 600,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE,
    used_at TIMESTAMP WITH TIME ZONE,
    created_by VARCHAR(255),
    recipient_email VARCHAR(255),
    recipient_hash VARCHAR(12),
    notes_hash VARCHAR(12),
    status VARCHAR(50) DEFAULT 'valid',
    max_uses INTEGER DEFAULT 1,
    use_count INTEGER DEFAULT 0,
    metadata JSONB DEFAULT '{}',
    
    CONSTRAINT check_invite_status CHECK (status IN ('valid', 'used', 'expired', 'revoked')),
    CONSTRAINT check_invite_ttl CHECK (ttl > 0 AND ttl <= 7200)
);

CREATE INDEX IF NOT EXISTS idx_invites_token ON invites(token);
CREATE INDEX IF NOT EXISTS idx_invites_status ON invites(status);
CREATE INDEX IF NOT EXISTS idx_invites_created_by ON invites(created_by);
CREATE INDEX IF NOT EXISTS idx_invites_expires ON invites(expires_at) WHERE status = 'valid';

-- ============================================================================
-- User Quotas Table
-- ============================================================================
CREATE TABLE IF NOT EXISTS user_quotas (
    user_id VARCHAR(255) PRIMARY KEY,
    role VARCHAR(50) DEFAULT 'default',
    max_sessions INTEGER DEFAULT 10,
    max_concurrent_sessions INTEGER DEFAULT 5,
    max_daily_sessions INTEGER DEFAULT 50,
    max_weekly_sessions INTEGER DEFAULT 200,
    max_session_ttl INTEGER DEFAULT 7200,
    max_daily_cpu_hours REAL DEFAULT 24.0,
    custom_limits JSONB,
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT check_role CHECK (role IN ('default', 'premium', 'admin', 'trial', 'staff'))
);

CREATE INDEX IF NOT EXISTS idx_quotas_role ON user_quotas(role);

-- ============================================================================
-- Usage Tracking Table
-- ============================================================================
CREATE TABLE IF NOT EXISTS usage_tracking (
    id BIGSERIAL PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    session_id VARCHAR(255),
    action VARCHAR(50) NOT NULL,
    metadata JSONB DEFAULT '{}',
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    source_ip INET,
    
    CONSTRAINT check_action CHECK (action IN (
        'session_created', 'session_destroyed', 'session_expired',
        'invite_created', 'invite_used', 'invite_revoked',
        'quota_exceeded', 'policy_denied', 'command_blocked'
    ))
);

CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_tracking(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage_tracking(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_usage_action ON usage_tracking(action);
CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_tracking(session_id);
CREATE INDEX IF NOT EXISTS idx_usage_user_timestamp ON usage_tracking(user_id, timestamp DESC);

-- GIN index for metadata
CREATE INDEX IF NOT EXISTS idx_usage_metadata ON usage_tracking USING GIN (metadata);

-- ============================================================================
-- Organizations Table
-- ============================================================================
CREATE TABLE IF NOT EXISTS organizations (
    org_id VARCHAR(255) PRIMARY KEY,
    org_name VARCHAR(255) NOT NULL,
    max_total_sessions INTEGER DEFAULT 100,
    max_total_concurrent INTEGER DEFAULT 50,
    max_daily_cpu_hours REAL DEFAULT 100.0,
    billing_email VARCHAR(255),
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_orgs_enabled ON organizations(enabled);

-- ============================================================================
-- User Organization Mapping
-- ============================================================================
CREATE TABLE IF NOT EXISTS user_org_mapping (
    user_id VARCHAR(255) NOT NULL,
    org_id VARCHAR(255) NOT NULL,
    role VARCHAR(50) DEFAULT 'member',
    joined_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, org_id),
    FOREIGN KEY (user_id) REFERENCES user_quotas(user_id) ON DELETE CASCADE,
    FOREIGN KEY (org_id) REFERENCES organizations(org_id) ON DELETE CASCADE,
    
    CONSTRAINT check_org_role CHECK (role IN ('owner', 'admin', 'member', 'viewer'))
);

CREATE INDEX IF NOT EXISTS idx_user_org_user ON user_org_mapping(user_id);
CREATE INDEX IF NOT EXISTS idx_user_org_org ON user_org_mapping(org_id);

-- ============================================================================
-- Audit Logs Table
-- ============================================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    event_type VARCHAR(100) NOT NULL,
    actor_id VARCHAR(255),
    actor_type VARCHAR(50) DEFAULT 'user',
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50),
    resource_id VARCHAR(255),
    details JSONB DEFAULT '{}',
    source_ip INET,
    user_agent TEXT,
    result VARCHAR(50) DEFAULT 'success',
    error_message TEXT,
    
    CONSTRAINT check_actor_type CHECK (actor_type IN ('user', 'system', 'service')),
    CONSTRAINT check_result CHECK (result IN ('success', 'failure', 'denied'))
);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_logs(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_logs(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_audit_result ON audit_logs(result);

-- GIN index for details
CREATE INDEX IF NOT EXISTS idx_audit_details ON audit_logs USING GIN (details);

-- ============================================================================
-- Metrics Table (for aggregated metrics)
-- ============================================================================
CREATE TABLE IF NOT EXISTS metrics_summary (
    id SERIAL PRIMARY KEY,
    metric_name VARCHAR(100) NOT NULL,
    metric_type VARCHAR(50) DEFAULT 'counter',
    value REAL NOT NULL,
    labels JSONB DEFAULT '{}',
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    period_start TIMESTAMP WITH TIME ZONE,
    period_end TIMESTAMP WITH TIME ZONE,
    
    CONSTRAINT check_metric_type CHECK (metric_type IN ('counter', 'gauge', 'histogram', 'summary'))
);

CREATE INDEX IF NOT EXISTS idx_metrics_name ON metrics_summary(metric_name);
CREATE INDEX IF NOT EXISTS idx_metrics_timestamp ON metrics_summary(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_period ON metrics_summary(period_start, period_end);

-- GIN index for labels
CREATE INDEX IF NOT EXISTS idx_metrics_labels ON metrics_summary USING GIN (labels);

-- ============================================================================
-- Policy Decisions Table
-- ============================================================================
CREATE TABLE IF NOT EXISTS policy_decisions (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    user_id VARCHAR(255),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50),
    resource_id VARCHAR(255),
    policy_path VARCHAR(255),
    decision BOOLEAN NOT NULL,
    reason TEXT,
    conditions JSONB DEFAULT '[]',
    obligations JSONB DEFAULT '[]',
    risk_score INTEGER DEFAULT 0,
    source_ip INET,
    
    CONSTRAINT check_decision_reason CHECK (
        (decision = true AND reason IS NOT NULL) OR
        (decision = false AND reason IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_policy_timestamp ON policy_decisions(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_policy_user ON policy_decisions(user_id);
CREATE INDEX IF NOT EXISTS idx_policy_decision ON policy_decisions(decision);
CREATE INDEX IF NOT EXISTS idx_policy_risk ON policy_decisions(risk_score);

-- ============================================================================
-- Rate Limits Table
-- ============================================================================
CREATE TABLE IF NOT EXISTS rate_limits (
    id SERIAL PRIMARY KEY,
    identifier VARCHAR(255) NOT NULL,  -- IP, user_id, etc.
    identifier_type VARCHAR(50) DEFAULT 'ip',
    endpoint VARCHAR(255),
    request_count INTEGER DEFAULT 1,
    window_start TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    window_end TIMESTAMP WITH TIME ZONE,
    limit_value INTEGER DEFAULT 100,
    
    UNIQUE(identifier, identifier_type, endpoint, window_start),
    CONSTRAINT check_identifier_type CHECK (identifier_type IN ('ip', 'user', 'session', 'org'))
);

CREATE INDEX IF NOT EXISTS idx_rate_limits_identifier ON rate_limits(identifier, identifier_type);
CREATE INDEX IF NOT EXISTS idx_rate_limits_window ON rate_limits(window_end);

-- ============================================================================
-- Functions
-- ============================================================================

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Function to calculate session expiration
CREATE OR REPLACE FUNCTION calculate_expires_at()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.ttl IS NOT NULL AND NEW.created_at IS NOT NULL THEN
        NEW.expires_at = NEW.created_at + (NEW.ttl || ' seconds')::INTERVAL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Function to cleanup old sessions
CREATE OR REPLACE FUNCTION cleanup_old_sessions(max_age_days INTEGER DEFAULT 30)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM sessions
    WHERE created_at < (CURRENT_TIMESTAMP - (max_age_days || ' days')::INTERVAL)
    AND status IN ('destroyed', 'ended', 'error', 'timeout');
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Function to cleanup old audit logs
CREATE OR REPLACE FUNCTION cleanup_old_audit_logs(max_age_days INTEGER DEFAULT 90)
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM audit_logs
    WHERE timestamp < (CURRENT_TIMESTAMP - (max_age_days || ' days')::INTERVAL);
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- Triggers
-- ============================================================================

-- Update updated_at on user_quotas
CREATE TRIGGER update_user_quotas_updated_at
    BEFORE UPDATE ON user_quotas
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Update updated_at on organizations
CREATE TRIGGER update_organizations_updated_at
    BEFORE UPDATE ON organizations
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Calculate expires_at on sessions
CREATE TRIGGER calculate_session_expires_at
    BEFORE INSERT OR UPDATE OF ttl, created_at ON sessions
    FOR EACH ROW
    EXECUTE FUNCTION calculate_expires_at();

-- ============================================================================
-- Views
-- ============================================================================

-- Active sessions view
CREATE OR REPLACE VIEW active_sessions AS
SELECT 
    session_id,
    container_name,
    ssh_host,
    ssh_port,
    ssh_user,
    profile,
    ttl,
    user_id,
    created_at,
    expires_at,
    EXTRACT(EPOCH FROM (expires_at - CURRENT_TIMESTAMP)) AS time_left_seconds,
    source_ip
FROM sessions
WHERE status = 'active'
AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP);

-- Daily usage summary view
CREATE OR REPLACE VIEW daily_usage_summary AS
SELECT 
    DATE(timestamp) AS usage_date,
    user_id,
    COUNT(*) FILTER (WHERE action = 'session_created') AS sessions_created,
    COUNT(*) FILTER (WHERE action = 'session_destroyed') AS sessions_destroyed,
    COUNT(*) FILTER (WHERE action = 'quota_exceeded') AS quota_exceeded_count,
    COUNT(*) FILTER (WHERE action = 'policy_denied') AS policy_denied_count
FROM usage_tracking
GROUP BY DATE(timestamp), user_id;

-- ============================================================================
-- Initial Data
-- ============================================================================

-- Insert default admin quota
INSERT INTO user_quotas (user_id, role, max_sessions, max_concurrent_sessions, max_daily_sessions, max_session_ttl)
VALUES ('admin@sshbox.local', 'admin', 100, 50, 500, 28800)
ON CONFLICT (user_id) DO NOTHING;

-- Insert default organization
INSERT INTO organizations (org_id, org_name, max_total_sessions, max_total_concurrent)
VALUES ('default', 'Default Organization', 100, 50)
ON CONFLICT (org_id) DO NOTHING;

-- ============================================================================
-- Comments
-- ============================================================================

COMMENT ON TABLE sessions IS 'Ephemeral SSH session records';
COMMENT ON TABLE session_recordings IS 'SSH session recording metadata';
COMMENT ON TABLE invites IS 'Invite tokens for session access';
COMMENT ON TABLE user_quotas IS 'User quota limits and roles';
COMMENT ON TABLE usage_tracking IS 'Usage event tracking for quotas and analytics';
COMMENT ON TABLE audit_logs IS 'Security audit log for all system events';
COMMENT ON TABLE policy_decisions IS 'Policy engine decision log';
