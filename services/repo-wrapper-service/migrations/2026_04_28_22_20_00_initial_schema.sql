-- Initial repo-wrapper-service database schema
-- Created: 2026-04-28 22:20:00

-- =====================================================
-- ADAPTER GENERATIONS (ASSISTED ADAPTER CREATION)
-- =====================================================

CREATE TABLE adapter_generations (
    generation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    intake_id UUID NOT NULL,
    family_id UUID NOT NULL,
    requested_by UUID NOT NULL,

    -- Source repository information
    github_url TEXT NOT NULL,
    github_owner VARCHAR(255) NOT NULL,
    github_repo VARCHAR(255) NOT NULL,
    commit_sha VARCHAR(64) NOT NULL,

    -- Generation status
    status VARCHAR(50) NOT NULL DEFAULT 'analyzing' CHECK (status IN (
        'analyzing',
        'drafting',
        'pending_user_review',
        'user_modifying',
        'approved',
        'rejected',
        'registered',
        'failed',
        'expired'
    )),

    priority INTEGER NOT NULL DEFAULT 5 CHECK (priority >= 1 AND priority <= 10),

    -- Repository analysis results
    repo_analysis JSONB NOT NULL DEFAULT '{}',
    security_summary JSONB DEFAULT '{}',

    -- Adapter drafts and modifications
    system_adapter_draft JSONB,
    user_modifications JSONB DEFAULT '{}',
    final_adapter_config JSONB,

    -- User interaction
    approved_by UUID,
    approved_at TIMESTAMP WITH TIME ZONE,
    rejected_by UUID,
    rejected_at TIMESTAMP WITH TIME ZONE,
    rejection_reason TEXT,

    -- Review and feedback
    user_feedback JSONB DEFAULT '{}',
    review_notes TEXT,
    modification_count INTEGER DEFAULT 0 CHECK (modification_count >= 0),

    -- Generation metadata
    generation_method VARCHAR(100) DEFAULT 'assisted',
    complexity_score DECIMAL(5,4) DEFAULT 0.0 CHECK (complexity_score >= 0.0 AND complexity_score <= 1.0),
    confidence_score DECIMAL(5,4) DEFAULT 1.0 CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0),

    -- Timing
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    analysis_started_at TIMESTAMP WITH TIME ZONE,
    draft_ready_at TIMESTAMP WITH TIME ZONE,
    user_review_started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    expires_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    -- Error handling
    error_message TEXT,
    retry_count INTEGER DEFAULT 0 CHECK (retry_count >= 0),
    max_retries INTEGER DEFAULT 2 CHECK (max_retries >= 0),

    metadata JSONB DEFAULT '{}'
);

-- Indexes for adapter generations
CREATE INDEX idx_adapter_generations_intake_id ON adapter_generations(intake_id);
CREATE INDEX idx_adapter_generations_family_id ON adapter_generations(family_id);
CREATE INDEX idx_adapter_generations_requested_by ON adapter_generations(requested_by);
CREATE INDEX idx_adapter_generations_status ON adapter_generations(status);
CREATE INDEX idx_adapter_generations_github_repo ON adapter_generations(github_owner, github_repo);
CREATE INDEX idx_adapter_generations_created_at ON adapter_generations(created_at DESC);
CREATE INDEX idx_adapter_generations_approved_by ON adapter_generations(approved_by);

-- Composite indexes for common queries
CREATE INDEX idx_adapter_generations_family_status ON adapter_generations(family_id, status, created_at DESC);
CREATE INDEX idx_adapter_generations_pending_review ON adapter_generations(family_id, priority, draft_ready_at ASC)
WHERE status = 'pending_user_review';

-- =====================================================
-- ADAPTER TEMPLATES (REUSABLE PATTERNS)
-- =====================================================

CREATE TABLE adapter_templates (
    template_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    template_name VARCHAR(100) UNIQUE NOT NULL,
    template_category VARCHAR(50) NOT NULL,

    -- Template matching criteria
    language_patterns JSONB DEFAULT '[]',
    framework_patterns JSONB DEFAULT '[]',
    file_patterns JSONB DEFAULT '[]',
    dependency_patterns JSONB DEFAULT '[]',

    -- Template configuration
    template_config JSONB NOT NULL,
    default_parameters JSONB DEFAULT '{}',

    -- Template metadata
    description TEXT,
    usage_count INTEGER DEFAULT 0 CHECK (usage_count >= 0),
    success_rate DECIMAL(5,4) DEFAULT 0.0 CHECK (success_rate >= 0.0 AND success_rate <= 1.0),
    complexity_level VARCHAR(20) DEFAULT 'medium' CHECK (complexity_level IN ('simple', 'medium', 'complex')),

    -- Template management
    enabled BOOLEAN DEFAULT true,
    version VARCHAR(20) DEFAULT '1.0',
    created_by VARCHAR(255),

    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    metadata JSONB DEFAULT '{}'
);

-- Indexes for adapter templates
CREATE INDEX idx_adapter_templates_category ON adapter_templates(template_category);
CREATE INDEX idx_adapter_templates_enabled ON adapter_templates(enabled);
CREATE INDEX idx_adapter_templates_success_rate ON adapter_templates(success_rate DESC);
CREATE INDEX idx_adapter_templates_usage_count ON adapter_templates(usage_count DESC);

-- GIN indexes for pattern matching
CREATE INDEX idx_adapter_templates_language_patterns ON adapter_templates USING GIN(language_patterns);
CREATE INDEX idx_adapter_templates_framework_patterns ON adapter_templates USING GIN(framework_patterns);
CREATE INDEX idx_adapter_templates_file_patterns ON adapter_templates USING GIN(file_patterns);

-- =====================================================
-- USER MODIFICATIONS (TRACK USER CHANGES)
-- =====================================================

CREATE TABLE user_modifications (
    modification_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    generation_id UUID NOT NULL,
    family_id UUID NOT NULL,
    modified_by UUID NOT NULL,

    -- Modification details
    modification_type VARCHAR(50) NOT NULL CHECK (modification_type IN (
        'parameter_change',
        'config_override',
        'template_switch',
        'custom_code',
        'security_exception',
        'dependency_change'
    )),

    -- Change tracking
    field_path TEXT NOT NULL,
    old_value JSONB,
    new_value JSONB NOT NULL,

    -- Modification context
    reason TEXT,
    validation_status VARCHAR(50) DEFAULT 'pending' CHECK (validation_status IN ('pending', 'valid', 'invalid', 'warning')),
    validation_message TEXT,

    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'
);

-- Indexes for user modifications
CREATE INDEX idx_user_modifications_generation_id ON user_modifications(generation_id);
CREATE INDEX idx_user_modifications_family_id ON user_modifications(family_id);
CREATE INDEX idx_user_modifications_modified_by ON user_modifications(modified_by);
CREATE INDEX idx_user_modifications_modification_type ON user_modifications(modification_type);
CREATE INDEX idx_user_modifications_created_at ON user_modifications(created_at DESC);

-- =====================================================
-- ADAPTER REGISTRATIONS (FINAL ADAPTER RECORDS)
-- =====================================================

CREATE TABLE adapter_registrations (
    registration_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    generation_id UUID NOT NULL,
    intake_id UUID NOT NULL,
    family_id UUID NOT NULL,

    -- Adapter identity
    adapter_name VARCHAR(255) NOT NULL,
    adapter_version VARCHAR(20) DEFAULT '1.0.0',
    adapter_namespace VARCHAR(100) NOT NULL,

    -- Source information
    source_repo_url TEXT NOT NULL,
    source_commit_sha VARCHAR(64) NOT NULL,

    -- Final adapter configuration
    final_adapter_config JSONB NOT NULL,
    generated_files JSONB DEFAULT '{}',

    -- Registration metadata
    registered_by UUID NOT NULL,
    registration_method VARCHAR(50) DEFAULT 'assisted',

    -- Quality metrics
    security_approved BOOLEAN DEFAULT false,
    test_coverage DECIMAL(5,4) DEFAULT 0.0 CHECK (test_coverage >= 0.0 AND test_coverage <= 1.0),
    quality_score DECIMAL(5,4) DEFAULT 0.0 CHECK (quality_score >= 0.0 AND quality_score <= 1.0),

    -- Status tracking
    status VARCHAR(50) DEFAULT 'registered' CHECK (status IN ('registered', 'active', 'deprecated', 'revoked')),

    registered_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at TIMESTAMP WITH TIME ZONE,
    deprecated_at TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE,

    metadata JSONB DEFAULT '{}'
);

-- Indexes for adapter registrations
CREATE INDEX idx_adapter_registrations_generation_id ON adapter_registrations(generation_id);
CREATE INDEX idx_adapter_registrations_intake_id ON adapter_registrations(intake_id);
CREATE INDEX idx_adapter_registrations_family_id ON adapter_registrations(family_id);
CREATE INDEX idx_adapter_registrations_adapter_name ON adapter_registrations(adapter_name);
CREATE INDEX idx_adapter_registrations_status ON adapter_registrations(status);
CREATE INDEX idx_adapter_registrations_registered_at ON adapter_registrations(registered_at DESC);

-- Unique constraint for adapter name within family
CREATE UNIQUE INDEX idx_adapter_registrations_unique_name ON adapter_registrations(family_id, adapter_namespace, adapter_name)
WHERE status = 'active';

-- =====================================================
-- GENERATION ANALYTICS (PERFORMANCE TRACKING)
-- =====================================================

CREATE TABLE generation_analytics (
    analytics_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    generation_id UUID NOT NULL,
    family_id UUID NOT NULL,

    -- Performance metrics
    analysis_duration_ms INTEGER CHECK (analysis_duration_ms >= 0),
    draft_generation_duration_ms INTEGER CHECK (draft_generation_duration_ms >= 0),
    total_duration_ms INTEGER CHECK (total_duration_ms >= 0),

    -- Quality metrics
    template_match_score DECIMAL(5,4) DEFAULT 0.0 CHECK (template_match_score >= 0.0 AND template_match_score <= 1.0),
    user_satisfaction_score DECIMAL(5,4) CHECK (user_satisfaction_score >= 0.0 AND user_satisfaction_score <= 5.0),

    -- Usage patterns
    modifications_made INTEGER DEFAULT 0 CHECK (modifications_made >= 0),
    review_time_minutes INTEGER CHECK (review_time_minutes >= 0),

    -- Outcome tracking
    outcome VARCHAR(50) NOT NULL CHECK (outcome IN ('approved', 'rejected', 'expired', 'error')),
    outcome_reason VARCHAR(100),

    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'
);

-- Indexes for generation analytics
CREATE INDEX idx_generation_analytics_generation_id ON generation_analytics(generation_id);
CREATE INDEX idx_generation_analytics_family_id ON generation_analytics(family_id);
CREATE INDEX idx_generation_analytics_outcome ON generation_analytics(outcome);
CREATE INDEX idx_generation_analytics_recorded_at ON generation_analytics(recorded_at DESC);

-- =====================================================
-- FOREIGN KEY CONSTRAINTS
-- =====================================================

-- Link user modifications to generations
ALTER TABLE user_modifications
ADD CONSTRAINT fk_user_modifications_generation_id
FOREIGN KEY (generation_id) REFERENCES adapter_generations(generation_id)
ON DELETE CASCADE;

-- Link registrations to generations
ALTER TABLE adapter_registrations
ADD CONSTRAINT fk_adapter_registrations_generation_id
FOREIGN KEY (generation_id) REFERENCES adapter_generations(generation_id)
ON DELETE CASCADE;

-- Link analytics to generations
ALTER TABLE generation_analytics
ADD CONSTRAINT fk_generation_analytics_generation_id
FOREIGN KEY (generation_id) REFERENCES adapter_generations(generation_id)
ON DELETE CASCADE;

-- =====================================================
-- TRIGGERS FOR AUTOMATIC UPDATES
-- =====================================================

-- Update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_adapter_generations_updated_at
    BEFORE UPDATE ON adapter_generations
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_adapter_templates_updated_at
    BEFORE UPDATE ON adapter_templates
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Set timing fields based on status changes
CREATE OR REPLACE FUNCTION set_generation_timing()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status != NEW.status THEN
        CASE NEW.status
            WHEN 'analyzing' THEN
                IF NEW.analysis_started_at IS NULL THEN
                    NEW.analysis_started_at = CURRENT_TIMESTAMP;
                END IF;
            WHEN 'drafting' THEN
                -- Keep analysis_started_at, don't override
                NULL;
            WHEN 'pending_user_review' THEN
                IF NEW.draft_ready_at IS NULL THEN
                    NEW.draft_ready_at = CURRENT_TIMESTAMP;
                END IF;
            WHEN 'user_modifying' THEN
                IF NEW.user_review_started_at IS NULL THEN
                    NEW.user_review_started_at = CURRENT_TIMESTAMP;
                END IF;
            WHEN 'approved', 'rejected', 'registered', 'failed', 'expired' THEN
                IF NEW.completed_at IS NULL THEN
                    NEW.completed_at = CURRENT_TIMESTAMP;
                END IF;
        END CASE;
    END IF;

    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER set_adapter_generations_timing
    BEFORE UPDATE ON adapter_generations
    FOR EACH ROW
    EXECUTE FUNCTION set_generation_timing();

-- Track modification count
CREATE OR REPLACE FUNCTION increment_modification_count()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE adapter_generations
    SET modification_count = modification_count + 1,
        updated_at = CURRENT_TIMESTAMP
    WHERE generation_id = NEW.generation_id;

    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER increment_modification_count_trigger
    AFTER INSERT ON user_modifications
    FOR EACH ROW
    EXECUTE FUNCTION increment_modification_count();

-- Update template usage statistics
CREATE OR REPLACE FUNCTION update_template_stats()
RETURNS TRIGGER AS $$
DECLARE
    template_used VARCHAR(100);
    generation_outcome VARCHAR(50);
BEGIN
    -- Extract template used from repo_analysis
    SELECT (repo_analysis->>'template_used') INTO template_used
    FROM adapter_generations WHERE generation_id = NEW.generation_id;

    SELECT status INTO generation_outcome
    FROM adapter_generations WHERE generation_id = NEW.generation_id;

    IF template_used IS NOT NULL THEN
        UPDATE adapter_templates SET
            usage_count = usage_count + 1,
            success_rate = CASE
                WHEN generation_outcome = 'registered' THEN
                    (success_rate * usage_count + 1.0) / (usage_count + 1)
                ELSE
                    (success_rate * usage_count) / (usage_count + 1)
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE template_name = template_used;
    END IF;

    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_template_stats_trigger
    AFTER INSERT ON generation_analytics
    FOR EACH ROW
    EXECUTE FUNCTION update_template_stats();

-- =====================================================
-- DATA INTEGRITY CONSTRAINTS
-- =====================================================

-- Ensure timing consistency
ALTER TABLE adapter_generations ADD CONSTRAINT check_generation_timing
CHECK (
    (completed_at IS NULL OR analysis_started_at IS NULL OR completed_at >= analysis_started_at) AND
    (draft_ready_at IS NULL OR analysis_started_at IS NULL OR draft_ready_at >= analysis_started_at) AND
    (completed_at IS NULL OR draft_ready_at IS NULL OR completed_at >= draft_ready_at)
);

-- Ensure approval/rejection consistency
ALTER TABLE adapter_generations ADD CONSTRAINT check_approval_consistency
CHECK (
    (status = 'approved' AND approved_by IS NOT NULL AND approved_at IS NOT NULL) OR
    (status != 'approved') OR
    (status = 'rejected' AND rejected_by IS NOT NULL AND rejected_at IS NOT NULL) OR
    (status != 'rejected')
);

-- Ensure retry count is within limits
ALTER TABLE adapter_generations ADD CONSTRAINT check_retry_limits
CHECK (retry_count <= max_retries);

-- Ensure valid GitHub URL format
ALTER TABLE adapter_generations ADD CONSTRAINT check_github_url_format
CHECK (github_url ~* '^https://github\.com/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+/?(\?.*)?$');

-- Ensure adapter name is valid
ALTER TABLE adapter_registrations ADD CONSTRAINT check_adapter_name_format
CHECK (adapter_name ~* '^[a-zA-Z0-9_-]+$');

-- Ensure namespace is valid
ALTER TABLE adapter_registrations ADD CONSTRAINT check_namespace_format
CHECK (adapter_namespace ~* '^[a-zA-Z0-9_.-]+$');

-- Ensure reasonable review time
ALTER TABLE generation_analytics ADD CONSTRAINT check_reasonable_review_time
CHECK (review_time_minutes IS NULL OR review_time_minutes >= 0 AND review_time_minutes <= 1440); -- Max 24 hours

-- =====================================================
-- PERFORMANCE OPTIMIZATIONS
-- =====================================================

-- Partial indexes for active processing
CREATE INDEX idx_adapter_generations_active ON adapter_generations(status, priority, created_at ASC)
WHERE status IN ('analyzing', 'drafting', 'pending_user_review');

CREATE INDEX idx_adapter_generations_user_pending ON adapter_generations(family_id, requested_by, draft_ready_at DESC)
WHERE status = 'pending_user_review';

CREATE INDEX idx_adapter_registrations_active_family ON adapter_registrations(family_id, status, registered_at DESC)
WHERE status = 'active';

-- =====================================================
-- DOCUMENTATION COMMENTS
-- =====================================================

COMMENT ON TABLE adapter_generations IS 'Assisted adapter generation workflow with user review and approval';
COMMENT ON TABLE adapter_templates IS 'Reusable adapter templates for common repository patterns';
COMMENT ON TABLE user_modifications IS 'Track user changes during adapter review process';
COMMENT ON TABLE adapter_registrations IS 'Final registered adapters ready for use';
COMMENT ON TABLE generation_analytics IS 'Performance and quality metrics for adapter generation';

COMMENT ON COLUMN adapter_generations.status IS 'Generation workflow status from analysis to registration';
COMMENT ON COLUMN adapter_generations.system_adapter_draft IS 'AI-generated adapter draft for user review';
COMMENT ON COLUMN adapter_generations.user_modifications IS 'User changes applied to the draft';
COMMENT ON COLUMN adapter_generations.final_adapter_config IS 'Final adapter configuration after approval';
COMMENT ON COLUMN adapter_templates.language_patterns IS 'JSON array of language detection patterns';
COMMENT ON COLUMN user_modifications.field_path IS 'JSONPath to the modified field in adapter config';
COMMENT ON COLUMN adapter_registrations.adapter_namespace IS 'Family-scoped namespace for adapter organization';