-- Initial repair-agent database schema
-- Created: 2026-04-28 21:00:00

-- =====================================================
-- REPAIR OPERATIONS (CORE FUNCTIONALITY)
-- =====================================================

CREATE TABLE repair_operations (
    operation_id BIGSERIAL PRIMARY KEY,
    repair_id VARCHAR(255) UNIQUE NOT NULL,
    agent_id VARCHAR(255) NOT NULL,
    execution_id VARCHAR(255) NOT NULL,
    repair_type VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    priority INTEGER NOT NULL DEFAULT 5 CHECK (priority >= 1 AND priority <= 10),
    auto_initiated BOOLEAN NOT NULL DEFAULT false,
    progress INTEGER DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    repair_data JSONB NOT NULL DEFAULT '{}',
    repair_result JSONB,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    timeout_seconds INTEGER DEFAULT 300
);

-- Indexes for repair operations
CREATE INDEX idx_repair_operations_agent_id ON repair_operations(agent_id);
CREATE INDEX idx_repair_operations_execution_id ON repair_operations(execution_id);
CREATE INDEX idx_repair_operations_repair_type ON repair_operations(repair_type);
CREATE INDEX idx_repair_operations_status ON repair_operations(status);
CREATE INDEX idx_repair_operations_priority ON repair_operations(priority);
CREATE INDEX idx_repair_operations_created_at ON repair_operations(created_at DESC);
CREATE INDEX idx_repair_operations_auto_initiated ON repair_operations(auto_initiated);

-- Composite indexes for common queries
CREATE INDEX idx_repair_operations_status_priority ON repair_operations(status, priority, created_at);
CREATE INDEX idx_repair_operations_agent_status ON repair_operations(agent_id, status, created_at DESC);

-- =====================================================
-- REPAIR STRATEGIES (STRATEGY DEFINITIONS)
-- =====================================================

CREATE TABLE repair_strategies (
    strategy_id BIGSERIAL PRIMARY KEY,
    strategy_name VARCHAR(100) UNIQUE NOT NULL,
    strategy_config JSONB NOT NULL DEFAULT '{}',
    description TEXT,
    success_rate DECIMAL(5,4) DEFAULT 0.0 CHECK (success_rate >= 0.0 AND success_rate <= 1.0),
    avg_duration_seconds DECIMAL(10,3) DEFAULT 0.0,
    usage_count INTEGER DEFAULT 0,
    enabled BOOLEAN DEFAULT true,
    min_retry_delay_seconds INTEGER DEFAULT 1,
    max_retry_delay_seconds INTEGER DEFAULT 60,
    max_retries INTEGER DEFAULT 3,
    registered_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for repair strategies
CREATE INDEX idx_repair_strategies_enabled ON repair_strategies(enabled);
CREATE INDEX idx_repair_strategies_success_rate ON repair_strategies(success_rate DESC);
CREATE INDEX idx_repair_strategies_usage_count ON repair_strategies(usage_count DESC);

-- =====================================================
-- AUTO-REPAIR CONFIGURATION
-- =====================================================

CREATE TABLE auto_repair_config (
    config_id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(255) UNIQUE NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT false,
    strategies JSONB NOT NULL DEFAULT '[]', -- Array of strategy names
    max_retries INTEGER NOT NULL DEFAULT 3,
    escalation_threshold INTEGER NOT NULL DEFAULT 2,
    cooldown_minutes INTEGER DEFAULT 30,
    max_concurrent_repairs INTEGER DEFAULT 1,
    priority_override INTEGER CHECK (priority_override >= 1 AND priority_override <= 10),
    config JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for auto-repair configuration
CREATE INDEX idx_auto_repair_config_enabled ON auto_repair_config(enabled);
CREATE INDEX idx_auto_repair_config_agent_id ON auto_repair_config(agent_id);

-- =====================================================
-- ERROR PATTERNS (ERROR ANALYSIS)
-- =====================================================

CREATE TABLE error_patterns (
    pattern_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(255) NOT NULL,
    execution_id VARCHAR(255) NOT NULL,
    error_type VARCHAR(100) NOT NULL,
    error_message TEXT NOT NULL,
    error_context JSONB DEFAULT '{}',
    repair_attempted BOOLEAN DEFAULT false,
    pattern_hash VARCHAR(64), -- Hash for deduplication
    frequency_count INTEGER DEFAULT 1,
    first_seen TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for error pattern analysis
CREATE INDEX idx_error_patterns_agent_id ON error_patterns(agent_id);
CREATE INDEX idx_error_patterns_execution_id ON error_patterns(execution_id);
CREATE INDEX idx_error_patterns_error_type ON error_patterns(error_type);
CREATE INDEX idx_error_patterns_occurred_at ON error_patterns(occurred_at DESC);
CREATE INDEX idx_error_patterns_pattern_hash ON error_patterns(pattern_hash);
CREATE INDEX idx_error_patterns_repair_attempted ON error_patterns(repair_attempted);

-- =====================================================
-- RECOVERY METRICS (DOWNTIME AND RECOVERY TRACKING)
-- =====================================================

CREATE TABLE recovery_metrics (
    metric_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(255) NOT NULL,
    failure_time TIMESTAMP WITH TIME ZONE NOT NULL,
    recovery_time TIMESTAMP WITH TIME ZONE NOT NULL,
    recovery_method VARCHAR(100) NOT NULL,
    downtime_seconds DECIMAL(10,3) NOT NULL CHECK (downtime_seconds >= 0),
    data_lost BOOLEAN DEFAULT false,
    recovery_quality VARCHAR(50) DEFAULT 'full' CHECK (recovery_quality IN ('full', 'partial', 'degraded')),
    automated_recovery BOOLEAN DEFAULT false,
    recovery_details JSONB DEFAULT '{}',
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for recovery metrics
CREATE INDEX idx_recovery_metrics_agent_id ON recovery_metrics(agent_id);
CREATE INDEX idx_recovery_metrics_failure_time ON recovery_metrics(failure_time);
CREATE INDEX idx_recovery_metrics_recovery_method ON recovery_metrics(recovery_method);
CREATE INDEX idx_recovery_metrics_recorded_at ON recovery_metrics(recorded_at DESC);
CREATE INDEX idx_recovery_metrics_downtime ON recovery_metrics(downtime_seconds);

-- =====================================================
-- REPAIR SCHEDULES (MAINTENANCE WINDOWS)
-- =====================================================

CREATE TABLE repair_schedules (
    schedule_id BIGSERIAL PRIMARY KEY,
    agent_id VARCHAR(255) NOT NULL,
    schedule_name VARCHAR(255) NOT NULL,
    schedule_type VARCHAR(50) NOT NULL CHECK (schedule_type IN ('maintenance', 'preventive', 'diagnostic')),
    cron_expression VARCHAR(100) NOT NULL,
    repair_strategies JSONB NOT NULL DEFAULT '[]',
    enabled BOOLEAN DEFAULT true,
    last_run TIMESTAMP WITH TIME ZONE,
    next_run TIMESTAMP WITH TIME ZONE,
    run_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    config JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for repair schedules
CREATE INDEX idx_repair_schedules_agent_id ON repair_schedules(agent_id);
CREATE INDEX idx_repair_schedules_enabled ON repair_schedules(enabled);
CREATE INDEX idx_repair_schedules_next_run ON repair_schedules(next_run) WHERE enabled = true;
CREATE INDEX idx_repair_schedules_schedule_type ON repair_schedules(schedule_type);

-- =====================================================
-- REPAIR NOTIFICATIONS (ALERTING)
-- =====================================================

CREATE TABLE repair_notifications (
    notification_id BIGSERIAL PRIMARY KEY,
    repair_id VARCHAR(255) NOT NULL,
    agent_id VARCHAR(255) NOT NULL,
    notification_type VARCHAR(50) NOT NULL CHECK (notification_type IN ('repair_started', 'repair_completed', 'repair_failed', 'escalation')),
    severity VARCHAR(20) NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warning', 'error', 'critical')),
    recipients JSONB NOT NULL DEFAULT '[]',
    message TEXT NOT NULL,
    notification_config JSONB DEFAULT '{}',
    sent BOOLEAN DEFAULT false,
    sent_at TIMESTAMP WITH TIME ZONE,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for notifications
CREATE INDEX idx_repair_notifications_repair_id ON repair_notifications(repair_id);
CREATE INDEX idx_repair_notifications_agent_id ON repair_notifications(agent_id);
CREATE INDEX idx_repair_notifications_sent ON repair_notifications(sent);
CREATE INDEX idx_repair_notifications_created_at ON repair_notifications(created_at DESC);
CREATE INDEX idx_repair_notifications_severity ON repair_notifications(severity);

-- =====================================================
-- REPAIR AUDIT LOG (COMPREHENSIVE AUDIT TRAIL)
-- =====================================================

CREATE TABLE repair_audit_log (
    audit_id BIGSERIAL PRIMARY KEY,
    repair_id VARCHAR(255),
    agent_id VARCHAR(255),
    operation VARCHAR(100) NOT NULL,
    operation_data JSONB DEFAULT '{}',
    performed_by VARCHAR(255),
    performed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_ip INET,
    user_agent TEXT,
    correlation_id VARCHAR(255),
    request_id VARCHAR(255)
);

-- Indexes for audit log
CREATE INDEX idx_repair_audit_log_repair_id ON repair_audit_log(repair_id);
CREATE INDEX idx_repair_audit_log_agent_id ON repair_audit_log(agent_id);
CREATE INDEX idx_repair_audit_log_performed_at ON repair_audit_log(performed_at DESC);
CREATE INDEX idx_repair_audit_log_operation ON repair_audit_log(operation);
CREATE INDEX idx_repair_audit_log_correlation_id ON repair_audit_log(correlation_id);

-- =====================================================
-- PERFORMANCE OPTIMIZATIONS
-- =====================================================

-- Partial indexes for active/pending operations
CREATE INDEX idx_repair_operations_active ON repair_operations(agent_id, created_at DESC)
WHERE status IN ('pending', 'running');

CREATE INDEX idx_repair_operations_failed_recent ON repair_operations(agent_id, repair_type, created_at DESC)
WHERE status = 'failed' AND created_at > (CURRENT_TIMESTAMP - INTERVAL '7 days');

-- Index for auto-repair candidates
CREATE INDEX idx_error_patterns_auto_repair_candidates ON error_patterns(agent_id, error_type, occurred_at DESC)
WHERE repair_attempted = false;

-- =====================================================
-- FOREIGN KEY CONSTRAINTS
-- =====================================================

-- Link notifications to repair operations
ALTER TABLE repair_notifications
ADD CONSTRAINT fk_repair_notifications_repair_id
FOREIGN KEY (repair_id) REFERENCES repair_operations(repair_id)
ON DELETE CASCADE;

-- Link audit log to repair operations (optional, can be null for system operations)
ALTER TABLE repair_audit_log
ADD CONSTRAINT fk_repair_audit_log_repair_id
FOREIGN KEY (repair_id) REFERENCES repair_operations(repair_id)
ON DELETE CASCADE
DEFERRABLE INITIALLY DEFERRED;

-- =====================================================
-- TRIGGERS FOR AUTOMATIC UPDATES
-- =====================================================

-- Update updated_at timestamp on repair operations
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_repair_operations_updated_at
    BEFORE UPDATE ON repair_operations
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_auto_repair_config_updated_at
    BEFORE UPDATE ON auto_repair_config
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_repair_strategies_updated_at
    BEFORE UPDATE ON repair_strategies
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_repair_schedules_updated_at
    BEFORE UPDATE ON repair_schedules
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Trigger to set started_at when repair status changes to running
CREATE OR REPLACE FUNCTION set_repair_started_at()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status != 'running' AND NEW.status = 'running' AND NEW.started_at IS NULL THEN
        NEW.started_at = CURRENT_TIMESTAMP;
    END IF;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER set_repair_operations_started_at
    BEFORE UPDATE ON repair_operations
    FOR EACH ROW
    EXECUTE FUNCTION set_repair_started_at();

-- =====================================================
-- DATA INTEGRITY CONSTRAINTS
-- =====================================================

-- Ensure repair completion time is after start time
ALTER TABLE repair_operations ADD CONSTRAINT check_repair_completion_time
CHECK (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at);

-- Ensure priority is within valid range
ALTER TABLE repair_operations ADD CONSTRAINT check_priority_range
CHECK (priority >= 1 AND priority <= 10);

-- Ensure progress is within valid range
ALTER TABLE repair_operations ADD CONSTRAINT check_progress_range
CHECK (progress >= 0 AND progress <= 100);

-- Ensure success rate is within valid range
ALTER TABLE repair_strategies ADD CONSTRAINT check_success_rate_range
CHECK (success_rate >= 0.0 AND success_rate <= 1.0);

-- Ensure non-negative values
ALTER TABLE repair_strategies ADD CONSTRAINT check_usage_count_non_negative
CHECK (usage_count >= 0);

ALTER TABLE repair_strategies ADD CONSTRAINT check_avg_duration_non_negative
CHECK (avg_duration_seconds >= 0.0);

ALTER TABLE recovery_metrics ADD CONSTRAINT check_downtime_non_negative
CHECK (downtime_seconds >= 0);

ALTER TABLE repair_schedules ADD CONSTRAINT check_counts_non_negative
CHECK (run_count >= 0 AND success_count >= 0 AND success_count <= run_count);

-- Ensure recovery time is after failure time
ALTER TABLE recovery_metrics ADD CONSTRAINT check_recovery_time_order
CHECK (recovery_time >= failure_time);

-- Ensure valid cooldown period
ALTER TABLE auto_repair_config ADD CONSTRAINT check_cooldown_positive
CHECK (cooldown_minutes > 0);

-- =====================================================
-- DOCUMENTATION COMMENTS
-- =====================================================

COMMENT ON TABLE repair_operations IS 'Core repair operations tracking with status, priority, and results';
COMMENT ON TABLE repair_strategies IS 'Repair strategy definitions with performance metrics';
COMMENT ON TABLE auto_repair_config IS 'Per-agent auto-repair configuration and settings';
COMMENT ON TABLE error_patterns IS 'Error pattern tracking for analysis and trend detection';
COMMENT ON TABLE recovery_metrics IS 'Agent recovery time and reliability metrics';
COMMENT ON TABLE repair_schedules IS 'Scheduled maintenance and preventive repair windows';
COMMENT ON TABLE repair_notifications IS 'Notification and alerting for repair events';
COMMENT ON TABLE repair_audit_log IS 'Comprehensive audit trail for all repair-related operations';

COMMENT ON COLUMN repair_operations.repair_id IS 'Unique identifier for the repair operation';
COMMENT ON COLUMN repair_operations.priority IS 'Repair priority (1=highest, 10=lowest)';
COMMENT ON COLUMN repair_operations.auto_initiated IS 'Whether repair was automatically initiated';
COMMENT ON COLUMN repair_strategies.success_rate IS 'Historical success rate (0.0-1.0)';
COMMENT ON COLUMN repair_strategies.avg_duration_seconds IS 'Average repair completion time';
COMMENT ON COLUMN auto_repair_config.escalation_threshold IS 'Number of failures before escalation';
COMMENT ON COLUMN error_patterns.pattern_hash IS 'Hash for error deduplication';
COMMENT ON COLUMN recovery_metrics.downtime_seconds IS 'Total downtime from failure to recovery';
COMMENT ON COLUMN recovery_metrics.recovery_quality IS 'Quality of recovery: full, partial, degraded';
COMMENT ON COLUMN repair_schedules.cron_expression IS 'Cron expression for scheduled repairs';
COMMENT ON COLUMN repair_notifications.severity IS 'Notification severity level';