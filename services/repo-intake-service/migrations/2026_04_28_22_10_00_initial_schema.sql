-- Initial repo-intake-service database schema
-- Created: 2026-04-28 22:10:00

-- =====================================================
-- REPO INTAKES (MAIN WORKFLOW ORCHESTRATION)
-- =====================================================

CREATE TABLE repo_intakes (
    intake_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id UUID NOT NULL,
    requested_by UUID NOT NULL,
    github_url TEXT NOT NULL,
    github_owner VARCHAR(255) NOT NULL,
    github_repo VARCHAR(255) NOT NULL,
    github_branch VARCHAR(255) DEFAULT 'main',
    github_commit_sha VARCHAR(64),

    status VARCHAR(50) NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending',
        'validating_url',
        'fetching',
        'artifact_ready',
        'scanning',
        'scan_completed',
        'scan_failed',
        'wrapper_requested',
        'draft_ready',
        'approved',
        'rejected',
        'failed'
    )),

    priority INTEGER NOT NULL DEFAULT 5 CHECK (priority >= 1 AND priority <= 10),

    -- Progress tracking
    progress_stage VARCHAR(50) DEFAULT 'initialization',
    progress_percent INTEGER DEFAULT 0 CHECK (progress_percent >= 0 AND progress_percent <= 100),

    -- Error handling
    error_message TEXT,
    retry_count INTEGER DEFAULT 0 CHECK (retry_count >= 0),
    max_retries INTEGER DEFAULT 3 CHECK (max_retries >= 0),

    -- Timing
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    timeout_seconds INTEGER DEFAULT 1800 CHECK (timeout_seconds > 0),

    -- Configuration and metadata
    scan_config JSONB DEFAULT '{}',
    metadata JSONB DEFAULT '{}'
);

-- Indexes for repo intakes
CREATE INDEX idx_repo_intakes_family_id ON repo_intakes(family_id);
CREATE INDEX idx_repo_intakes_requested_by ON repo_intakes(requested_by);
CREATE INDEX idx_repo_intakes_status ON repo_intakes(status);
CREATE INDEX idx_repo_intakes_github_repo ON repo_intakes(github_owner, github_repo);
CREATE INDEX idx_repo_intakes_created_at ON repo_intakes(created_at DESC);
CREATE INDEX idx_repo_intakes_priority ON repo_intakes(priority);

-- Composite indexes for common queries
CREATE INDEX idx_repo_intakes_family_status ON repo_intakes(family_id, status, created_at DESC);
CREATE INDEX idx_repo_intakes_active ON repo_intakes(status, priority, created_at ASC)
WHERE status IN ('pending', 'validating_url', 'fetching', 'scanning');

-- =====================================================
-- REPO ARTIFACTS (DOWNLOADED REPO SNAPSHOTS)
-- =====================================================

CREATE TABLE repo_artifacts (
    artifact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    intake_id UUID NOT NULL,

    -- Source information
    source_url TEXT NOT NULL,
    commit_sha VARCHAR(64) NOT NULL,
    branch VARCHAR(255) NOT NULL,

    -- Artifact storage
    artifact_uri TEXT NOT NULL,
    storage_type VARCHAR(50) NOT NULL DEFAULT 'local' CHECK (storage_type IN ('local', 's3', 'gcs')),

    -- Repository metadata
    repo_size_bytes BIGINT CHECK (repo_size_bytes >= 0),
    file_count INTEGER CHECK (file_count >= 0),
    directory_count INTEGER CHECK (directory_count >= 0),

    -- Analysis results
    languages JSONB DEFAULT '[]',
    frameworks JSONB DEFAULT '[]',
    package_managers JSONB DEFAULT '[]',

    -- Security pre-analysis
    has_dotfiles BOOLEAN DEFAULT false,
    has_config_files BOOLEAN DEFAULT false,
    has_dependencies BOOLEAN DEFAULT false,

    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'
);

-- Indexes for repo artifacts
CREATE INDEX idx_repo_artifacts_intake_id ON repo_artifacts(intake_id);
CREATE INDEX idx_repo_artifacts_commit_sha ON repo_artifacts(commit_sha);
CREATE INDEX idx_repo_artifacts_created_at ON repo_artifacts(created_at DESC);
CREATE INDEX idx_repo_artifacts_storage_type ON repo_artifacts(storage_type);

-- GIN index for language/framework searches
CREATE INDEX idx_repo_artifacts_languages ON repo_artifacts USING GIN(languages);
CREATE INDEX idx_repo_artifacts_frameworks ON repo_artifacts USING GIN(frameworks);

-- =====================================================
-- SCAN JOBS (SECURITY SCANNER ORCHESTRATION)
-- =====================================================

CREATE TABLE scan_jobs (
    job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    intake_id UUID NOT NULL,
    artifact_id UUID NOT NULL,
    family_id UUID NOT NULL,

    scanner_type VARCHAR(100) NOT NULL CHECK (scanner_type IN (
        'malware-scanner',
        'license-checker',
        'cve-scanner',
        'content-policy-checker',
        'prompt-injection-detector'
    )),

    status VARCHAR(50) NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending',
        'dispatched',
        'running',
        'completed',
        'failed',
        'timeout',
        'cancelled'
    )),

    priority INTEGER NOT NULL DEFAULT 5 CHECK (priority >= 1 AND priority <= 10),

    -- Timing
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    dispatched_at TIMESTAMP WITH TIME ZONE,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    timeout_at TIMESTAMP WITH TIME ZONE,

    -- Job configuration
    scanner_config JSONB DEFAULT '{}',
    retry_count INTEGER DEFAULT 0 CHECK (retry_count >= 0),
    max_retries INTEGER DEFAULT 2 CHECK (max_retries >= 0),

    -- Worker assignment
    worker_id VARCHAR(255),
    worker_version VARCHAR(50),

    metadata JSONB DEFAULT '{}'
);

-- Indexes for scan jobs
CREATE INDEX idx_scan_jobs_intake_id ON scan_jobs(intake_id);
CREATE INDEX idx_scan_jobs_artifact_id ON scan_jobs(artifact_id);
CREATE INDEX idx_scan_jobs_family_id ON scan_jobs(family_id);
CREATE INDEX idx_scan_jobs_scanner_type ON scan_jobs(scanner_type);
CREATE INDEX idx_scan_jobs_status ON scan_jobs(status);
CREATE INDEX idx_scan_jobs_created_at ON scan_jobs(created_at DESC);

-- Composite indexes for job processing
CREATE INDEX idx_scan_jobs_pending ON scan_jobs(scanner_type, priority, created_at ASC)
WHERE status = 'pending';

CREATE INDEX idx_scan_jobs_active ON scan_jobs(scanner_type, status, created_at DESC)
WHERE status IN ('dispatched', 'running');

CREATE INDEX idx_scan_jobs_intake_status ON scan_jobs(intake_id, scanner_type, status);

-- =====================================================
-- SCAN RESULTS (SECURITY FINDINGS)
-- =====================================================

CREATE TABLE scan_results (
    result_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id UUID NOT NULL,
    intake_id UUID NOT NULL,
    artifact_id UUID NOT NULL,
    scanner_type VARCHAR(100) NOT NULL,

    -- Result summary
    status VARCHAR(50) NOT NULL CHECK (status IN ('clean', 'findings', 'error')),
    severity VARCHAR(20) NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical')),
    findings_count INTEGER DEFAULT 0 CHECK (findings_count >= 0),

    -- Detailed results
    findings JSONB DEFAULT '[]',
    scan_metadata JSONB DEFAULT '{}',

    -- Performance metrics
    scan_duration_ms INTEGER CHECK (scan_duration_ms >= 0),
    files_scanned INTEGER CHECK (files_scanned >= 0),
    bytes_scanned BIGINT CHECK (bytes_scanned >= 0),

    -- Quality metrics
    confidence_score DECIMAL(5,4) DEFAULT 1.0 CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0),
    false_positive_risk VARCHAR(20) DEFAULT 'low' CHECK (false_positive_risk IN ('low', 'medium', 'high')),

    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'
);

-- Indexes for scan results
CREATE INDEX idx_scan_results_job_id ON scan_results(job_id);
CREATE INDEX idx_scan_results_intake_id ON scan_results(intake_id);
CREATE INDEX idx_scan_results_artifact_id ON scan_results(artifact_id);
CREATE INDEX idx_scan_results_scanner_type ON scan_results(scanner_type);
CREATE INDEX idx_scan_results_status ON scan_results(status);
CREATE INDEX idx_scan_results_severity ON scan_results(severity);
CREATE INDEX idx_scan_results_created_at ON scan_results(created_at DESC);

-- Composite indexes for security analysis
CREATE INDEX idx_scan_results_intake_scanner ON scan_results(intake_id, scanner_type, severity DESC);
CREATE INDEX idx_scan_results_findings ON scan_results(scanner_type, severity, findings_count DESC)
WHERE findings_count > 0;

-- GIN index for findings search
CREATE INDEX idx_scan_results_findings_gin ON scan_results USING GIN(findings);

-- =====================================================
-- SCAN SUMMARIES (AGGREGATED SECURITY REPORTS)
-- =====================================================

CREATE TABLE scan_summaries (
    summary_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    intake_id UUID NOT NULL,
    artifact_id UUID NOT NULL,
    family_id UUID NOT NULL,

    -- Overall scan status
    overall_status VARCHAR(50) NOT NULL CHECK (overall_status IN ('scanning', 'completed', 'failed', 'partial')),
    overall_severity VARCHAR(20) NOT NULL DEFAULT 'info' CHECK (overall_severity IN ('info', 'low', 'medium', 'high', 'critical')),

    -- Scanner completion tracking
    scanners_total INTEGER NOT NULL CHECK (scanners_total > 0),
    scanners_completed INTEGER DEFAULT 0 CHECK (scanners_completed >= 0),
    scanners_failed INTEGER DEFAULT 0 CHECK (scanners_failed >= 0),

    -- Aggregated findings
    total_findings INTEGER DEFAULT 0 CHECK (total_findings >= 0),
    critical_findings INTEGER DEFAULT 0 CHECK (critical_findings >= 0),
    high_findings INTEGER DEFAULT 0 CHECK (high_findings >= 0),
    medium_findings INTEGER DEFAULT 0 CHECK (medium_findings >= 0),
    low_findings INTEGER DEFAULT 0 CHECK (low_findings >= 0),

    -- Security action required
    action_required VARCHAR(50) NOT NULL DEFAULT 'none' CHECK (action_required IN ('none', 'advisory', 'approval_required', 'blocked')),
    blocking_findings JSONB DEFAULT '[]',

    -- Summary data
    scanner_summaries JSONB DEFAULT '{}',
    recommendations JSONB DEFAULT '[]',

    -- Timing
    scan_started_at TIMESTAMP WITH TIME ZONE,
    scan_completed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,

    metadata JSONB DEFAULT '{}'
);

-- Indexes for scan summaries
CREATE INDEX idx_scan_summaries_intake_id ON scan_summaries(intake_id);
CREATE INDEX idx_scan_summaries_artifact_id ON scan_summaries(artifact_id);
CREATE INDEX idx_scan_summaries_family_id ON scan_summaries(family_id);
CREATE INDEX idx_scan_summaries_overall_status ON scan_summaries(overall_status);
CREATE INDEX idx_scan_summaries_overall_severity ON scan_summaries(overall_severity);
CREATE INDEX idx_scan_summaries_action_required ON scan_summaries(action_required);
CREATE INDEX idx_scan_summaries_created_at ON scan_summaries(created_at DESC);

-- =====================================================
-- FOREIGN KEY CONSTRAINTS
-- =====================================================

-- Link artifacts to intakes
ALTER TABLE repo_artifacts
ADD CONSTRAINT fk_repo_artifacts_intake_id
FOREIGN KEY (intake_id) REFERENCES repo_intakes(intake_id)
ON DELETE CASCADE;

-- Link scan jobs to intakes and artifacts
ALTER TABLE scan_jobs
ADD CONSTRAINT fk_scan_jobs_intake_id
FOREIGN KEY (intake_id) REFERENCES repo_intakes(intake_id)
ON DELETE CASCADE;

ALTER TABLE scan_jobs
ADD CONSTRAINT fk_scan_jobs_artifact_id
FOREIGN KEY (artifact_id) REFERENCES repo_artifacts(artifact_id)
ON DELETE CASCADE;

-- Link scan results to jobs, intakes, and artifacts
ALTER TABLE scan_results
ADD CONSTRAINT fk_scan_results_job_id
FOREIGN KEY (job_id) REFERENCES scan_jobs(job_id)
ON DELETE CASCADE;

ALTER TABLE scan_results
ADD CONSTRAINT fk_scan_results_intake_id
FOREIGN KEY (intake_id) REFERENCES repo_intakes(intake_id)
ON DELETE CASCADE;

ALTER TABLE scan_results
ADD CONSTRAINT fk_scan_results_artifact_id
FOREIGN KEY (artifact_id) REFERENCES repo_artifacts(artifact_id)
ON DELETE CASCADE;

-- Link scan summaries to intakes and artifacts
ALTER TABLE scan_summaries
ADD CONSTRAINT fk_scan_summaries_intake_id
FOREIGN KEY (intake_id) REFERENCES repo_intakes(intake_id)
ON DELETE CASCADE;

ALTER TABLE scan_summaries
ADD CONSTRAINT fk_scan_summaries_artifact_id
FOREIGN KEY (artifact_id) REFERENCES repo_artifacts(artifact_id)
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

CREATE TRIGGER update_repo_intakes_updated_at
    BEFORE UPDATE ON repo_intakes
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_scan_summaries_updated_at
    BEFORE UPDATE ON scan_summaries
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Set started_at when status changes to active states
CREATE OR REPLACE FUNCTION set_started_at()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status != NEW.status THEN
        CASE NEW.status
            WHEN 'fetching' THEN
                IF NEW.started_at IS NULL THEN NEW.started_at = CURRENT_TIMESTAMP; END IF;
            WHEN 'scanning' THEN
                IF NEW.started_at IS NULL THEN NEW.started_at = CURRENT_TIMESTAMP; END IF;
            WHEN 'running' THEN
                IF NEW.started_at IS NULL THEN NEW.started_at = CURRENT_TIMESTAMP; END IF;
        END CASE;

        -- Set completed_at for terminal states
        CASE NEW.status
            WHEN 'completed', 'failed', 'approved', 'rejected', 'cancelled', 'timeout' THEN
                IF NEW.completed_at IS NULL THEN NEW.completed_at = CURRENT_TIMESTAMP; END IF;
        END CASE;
    END IF;

    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER set_repo_intakes_timing
    BEFORE UPDATE ON repo_intakes
    FOR EACH ROW
    EXECUTE FUNCTION set_started_at();

CREATE TRIGGER set_scan_jobs_timing
    BEFORE UPDATE ON scan_jobs
    FOR EACH ROW
    EXECUTE FUNCTION set_started_at();

-- Auto-update scan summary when scan results change
CREATE OR REPLACE FUNCTION update_scan_summary()
RETURNS TRIGGER AS $$
DECLARE
    summary_record scan_summaries%ROWTYPE;
    total_scanners INTEGER;
    completed_scanners INTEGER;
    failed_scanners INTEGER;
    max_severity VARCHAR(20);
    total_findings_count INTEGER;
    findings_by_severity JSONB;
BEGIN
    -- Get current summary
    SELECT * INTO summary_record
    FROM scan_summaries
    WHERE intake_id = NEW.intake_id;

    IF NOT FOUND THEN
        RETURN NEW; -- Summary doesn't exist yet
    END IF;

    -- Count scanners
    SELECT COUNT(*),
           COUNT(*) FILTER (WHERE status = 'completed'),
           COUNT(*) FILTER (WHERE status = 'failed')
    INTO total_scanners, completed_scanners, failed_scanners
    FROM scan_jobs
    WHERE intake_id = NEW.intake_id;

    -- Get aggregated findings
    SELECT
        COALESCE(MAX(CASE severity
            WHEN 'critical' THEN 5
            WHEN 'high' THEN 4
            WHEN 'medium' THEN 3
            WHEN 'low' THEN 2
            ELSE 1 END), 1),
        SUM(findings_count),
        jsonb_object_agg(severity, severity_count)
    INTO max_severity, total_findings_count, findings_by_severity
    FROM (
        SELECT severity, COUNT(*) as severity_count, SUM(findings_count) as findings_count
        FROM scan_results
        WHERE intake_id = NEW.intake_id
        GROUP BY severity
    ) severity_counts;

    -- Convert max severity back to string
    max_severity := CASE max_severity
        WHEN 5 THEN 'critical'
        WHEN 4 THEN 'high'
        WHEN 3 THEN 'medium'
        WHEN 2 THEN 'low'
        ELSE 'info'
    END;

    -- Update summary
    UPDATE scan_summaries SET
        scanners_completed = completed_scanners,
        scanners_failed = failed_scanners,
        overall_severity = max_severity,
        total_findings = COALESCE(total_findings_count, 0),
        critical_findings = COALESCE((findings_by_severity->>'critical')::INTEGER, 0),
        high_findings = COALESCE((findings_by_severity->>'high')::INTEGER, 0),
        medium_findings = COALESCE((findings_by_severity->>'medium')::INTEGER, 0),
        low_findings = COALESCE((findings_by_severity->>'low')::INTEGER, 0),
        overall_status = CASE
            WHEN completed_scanners = total_scanners THEN 'completed'
            WHEN failed_scanners = total_scanners THEN 'failed'
            WHEN completed_scanners + failed_scanners = total_scanners THEN 'partial'
            ELSE 'scanning'
        END,
        updated_at = CURRENT_TIMESTAMP
    WHERE intake_id = NEW.intake_id;

    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_scan_summary_trigger
    AFTER INSERT OR UPDATE ON scan_results
    FOR EACH ROW
    EXECUTE FUNCTION update_scan_summary();

-- =====================================================
-- DATA INTEGRITY CONSTRAINTS
-- =====================================================

-- Ensure timing consistency
ALTER TABLE repo_intakes ADD CONSTRAINT check_intake_timing
CHECK (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at);

ALTER TABLE scan_jobs ADD CONSTRAINT check_job_timing
CHECK (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at);

-- Ensure retry counts are reasonable
ALTER TABLE repo_intakes ADD CONSTRAINT check_retry_counts
CHECK (retry_count <= max_retries);

ALTER TABLE scan_jobs ADD CONSTRAINT check_job_retry_counts
CHECK (retry_count <= max_retries);

-- Ensure scanner completion counts are consistent
ALTER TABLE scan_summaries ADD CONSTRAINT check_scanner_counts
CHECK (scanners_completed + scanners_failed <= scanners_total);

-- Ensure GitHub URL is valid format
ALTER TABLE repo_intakes ADD CONSTRAINT check_github_url_format
CHECK (github_url ~* '^https://github\.com/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+/?(\?.*)?$');

-- =====================================================
-- PERFORMANCE OPTIMIZATIONS
-- =====================================================

-- Partial indexes for active processing
CREATE INDEX idx_repo_intakes_processing ON repo_intakes(priority, created_at ASC)
WHERE status IN ('pending', 'validating_url', 'fetching', 'scanning');

CREATE INDEX idx_scan_jobs_queue ON scan_jobs(scanner_type, priority, created_at ASC)
WHERE status = 'pending';

CREATE INDEX idx_scan_summaries_active ON scan_summaries(family_id, overall_status, created_at DESC)
WHERE overall_status IN ('scanning', 'completed');

-- =====================================================
-- DOCUMENTATION COMMENTS
-- =====================================================

COMMENT ON TABLE repo_intakes IS 'Main workflow orchestration for GitHub repository processing';
COMMENT ON TABLE repo_artifacts IS 'Downloaded repository snapshots for scanner processing';
COMMENT ON TABLE scan_jobs IS 'Individual security scanner job tracking';
COMMENT ON TABLE scan_results IS 'Security scanner findings and results';
COMMENT ON TABLE scan_summaries IS 'Aggregated security reports per repository intake';

COMMENT ON COLUMN repo_intakes.status IS 'Workflow status through intake pipeline';
COMMENT ON COLUMN repo_intakes.scan_config IS 'Scanner configuration and thresholds';
COMMENT ON COLUMN repo_artifacts.artifact_uri IS 'Storage location of downloaded repository';
COMMENT ON COLUMN scan_jobs.scanner_type IS 'Type of security scanner to run';
COMMENT ON COLUMN scan_results.findings IS 'Detailed security findings in structured format';
COMMENT ON COLUMN scan_summaries.action_required IS 'User action required: none, advisory, approval_required, blocked';