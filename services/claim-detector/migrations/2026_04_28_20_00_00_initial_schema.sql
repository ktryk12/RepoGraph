-- Initial claim-detector database schema
-- Created: 2026-04-28 20:00:00

-- =====================================================
-- DETECTED CLAIMS STORAGE
-- =====================================================

CREATE TABLE detected_claims (
    claim_id VARCHAR(255) PRIMARY KEY,
    raw_text TEXT NOT NULL,
    source_url VARCHAR(2048) NOT NULL,
    platform VARCHAR(100) NOT NULL,
    detected_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    virality_score DECIMAL(5,4) NOT NULL DEFAULT 0.0,
    controversy_score DECIMAL(5,4) NOT NULL DEFAULT 0.0,
    factcheckability_score DECIMAL(5,4) NOT NULL DEFAULT 0.0,
    composite_score DECIMAL(5,4) NOT NULL DEFAULT 0.0,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for efficient querying
CREATE INDEX idx_detected_claims_platform ON detected_claims(platform);
CREATE INDEX idx_detected_claims_detected_at ON detected_claims(detected_at DESC);
CREATE INDEX idx_detected_claims_composite_score ON detected_claims(composite_score DESC);
CREATE INDEX idx_detected_claims_platform_score ON detected_claims(platform, composite_score DESC);

-- =====================================================
-- CLAIM DEDUPLICATION STORAGE
-- =====================================================

CREATE TABLE claim_dedupe (
    fingerprint VARCHAR(64) PRIMARY KEY,  -- SHA-256 hash
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Index for cleanup operations
CREATE INDEX idx_claim_dedupe_expires_at ON claim_dedupe(expires_at);

-- =====================================================
-- SCANNER PERFORMANCE ANALYTICS
-- =====================================================

CREATE TABLE scanner_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scanner_id VARCHAR(255) NOT NULL,
    platform VARCHAR(100) NOT NULL,
    run_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    candidates_found INTEGER NOT NULL DEFAULT 0,
    claims_emitted INTEGER NOT NULL DEFAULT 0,
    claims_skipped_dup INTEGER NOT NULL DEFAULT 0,
    claims_skipped_score INTEGER NOT NULL DEFAULT 0,
    scan_duration_seconds DECIMAL(10,3) NOT NULL DEFAULT 0.0,
    error_message TEXT,
    scanner_config JSONB DEFAULT '{}'
);

-- Indexes for analytics queries
CREATE INDEX idx_scanner_runs_platform ON scanner_runs(platform);
CREATE INDEX idx_scanner_runs_run_at ON scanner_runs(run_at DESC);
CREATE INDEX idx_scanner_runs_platform_run_at ON scanner_runs(platform, run_at DESC);

-- =====================================================
-- PLATFORM HEALTH MONITORING
-- =====================================================

CREATE TABLE platform_health (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform VARCHAR(100) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(50) NOT NULL,  -- healthy, degraded, error
    response_time_ms DECIMAL(10,2),
    rate_limit_remaining INTEGER,
    rate_limit_reset_at TIMESTAMP WITH TIME ZONE,
    error_details TEXT,
    api_quota_used INTEGER,
    api_quota_limit INTEGER
);

-- Indexes for health monitoring
CREATE INDEX idx_platform_health_platform ON platform_health(platform);
CREATE INDEX idx_platform_health_timestamp ON platform_health(timestamp DESC);
CREATE INDEX idx_platform_health_platform_timestamp ON platform_health(platform, timestamp DESC);
CREATE INDEX idx_platform_health_status ON platform_health(status);

-- =====================================================
-- DATA RETENTION AND CONSTRAINTS
-- =====================================================

-- Add constraints for score ranges (0.0 to 1.0)
ALTER TABLE detected_claims ADD CONSTRAINT check_virality_score
    CHECK (virality_score >= 0.0 AND virality_score <= 1.0);
ALTER TABLE detected_claims ADD CONSTRAINT check_controversy_score
    CHECK (controversy_score >= 0.0 AND controversy_score <= 1.0);
ALTER TABLE detected_claims ADD CONSTRAINT check_factcheckability_score
    CHECK (factcheckability_score >= 0.0 AND factcheckability_score <= 1.0);
ALTER TABLE detected_claims ADD CONSTRAINT check_composite_score
    CHECK (composite_score >= 0.0 AND composite_score <= 1.0);

-- Add constraints for platform health status
ALTER TABLE platform_health ADD CONSTRAINT check_platform_health_status
    CHECK (status IN ('healthy', 'degraded', 'error'));

-- Add constraints for non-negative counts
ALTER TABLE scanner_runs ADD CONSTRAINT check_candidates_found_non_negative
    CHECK (candidates_found >= 0);
ALTER TABLE scanner_runs ADD CONSTRAINT check_claims_emitted_non_negative
    CHECK (claims_emitted >= 0);
ALTER TABLE scanner_runs ADD CONSTRAINT check_claims_skipped_dup_non_negative
    CHECK (claims_skipped_dup >= 0);
ALTER TABLE scanner_runs ADD CONSTRAINT check_claims_skipped_score_non_negative
    CHECK (claims_skipped_score >= 0);

-- Comments for documentation
COMMENT ON TABLE detected_claims IS 'Stores all detected claims with scoring and metadata';
COMMENT ON TABLE claim_dedupe IS 'Deduplication fingerprints with TTL for claim uniqueness';
COMMENT ON TABLE scanner_runs IS 'Performance analytics for platform scanners';
COMMENT ON TABLE platform_health IS 'Health monitoring for external platform APIs';

COMMENT ON COLUMN detected_claims.claim_id IS 'Unique identifier for the detected claim';
COMMENT ON COLUMN detected_claims.composite_score IS 'Weighted combination of virality, controversy, and factcheckability';
COMMENT ON COLUMN claim_dedupe.fingerprint IS 'SHA-256 hash of normalized claim text';
COMMENT ON COLUMN scanner_runs.scan_duration_seconds IS 'Time taken to complete the scan operation';
COMMENT ON COLUMN platform_health.response_time_ms IS 'API response time in milliseconds';