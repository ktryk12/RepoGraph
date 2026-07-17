-- Initial lora-orchestrator database schema
-- Created: 2026-04-28 20:15:00

-- =====================================================
-- GAP REPORTS
-- =====================================================

CREATE TABLE gap_reports (
    gap_id VARCHAR(255) PRIMARY KEY,
    domain VARCHAR(255) NOT NULL,
    severity VARCHAR(50) NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    evidence JSONB NOT NULL DEFAULT '[]',
    status VARCHAR(50) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'resolved', 'deferred')),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for efficient querying
CREATE INDEX idx_gap_reports_domain ON gap_reports(domain);
CREATE INDEX idx_gap_reports_status ON gap_reports(status);
CREATE INDEX idx_gap_reports_severity ON gap_reports(severity);
CREATE INDEX idx_gap_reports_created_at ON gap_reports(created_at DESC);
CREATE INDEX idx_gap_reports_status_severity ON gap_reports(status, severity);

-- =====================================================
-- ADAPTER CANDIDATES
-- =====================================================

CREATE TABLE adapter_candidates (
    candidate_id VARCHAR(255) PRIMARY KEY,
    source_url VARCHAR(2048) NOT NULL,
    license_type VARCHAR(255) NOT NULL,
    base_model VARCHAR(255) NOT NULL,
    param_count BIGINT NOT NULL DEFAULT 0,
    last_updated TIMESTAMP WITH TIME ZONE NOT NULL,
    file_path VARCHAR(1024) NOT NULL,
    file_format VARCHAR(50) NOT NULL CHECK (file_format IN ('safetensors', 'pickle', 'other')),
    domain VARCHAR(255) NOT NULL,
    fetched_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}'
);

-- Indexes for adapter queries
CREATE INDEX idx_adapter_candidates_domain ON adapter_candidates(domain);
CREATE INDEX idx_adapter_candidates_base_model ON adapter_candidates(base_model);
CREATE INDEX idx_adapter_candidates_param_count ON adapter_candidates(param_count DESC);
CREATE INDEX idx_adapter_candidates_fetched_at ON adapter_candidates(fetched_at DESC);
CREATE INDEX idx_adapter_candidates_domain_params ON adapter_candidates(domain, param_count DESC);

-- =====================================================
-- SECURITY EVALUATIONS
-- =====================================================

CREATE TABLE security_evaluations (
    evaluation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id VARCHAR(255) NOT NULL REFERENCES adapter_candidates(candidate_id),
    gap_id VARCHAR(255) NOT NULL REFERENCES gap_reports(gap_id),
    s6_passed BOOLEAN NOT NULL DEFAULT false,
    s7_passed BOOLEAN NOT NULL DEFAULT false,
    s8_passed BOOLEAN NOT NULL DEFAULT false,
    overall_score DECIMAL(5,4) NOT NULL DEFAULT 0.0 CHECK (overall_score >= 0.0 AND overall_score <= 1.0),
    disqualification_reason VARCHAR(500),
    evaluation_details JSONB DEFAULT '{}',
    evaluated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for security evaluation queries
CREATE INDEX idx_security_evaluations_candidate_id ON security_evaluations(candidate_id);
CREATE INDEX idx_security_evaluations_gap_id ON security_evaluations(gap_id);
CREATE INDEX idx_security_evaluations_overall_score ON security_evaluations(overall_score DESC);
CREATE INDEX idx_security_evaluations_evaluated_at ON security_evaluations(evaluated_at DESC);
CREATE INDEX idx_security_evaluations_all_passed ON security_evaluations(s6_passed, s7_passed, s8_passed) WHERE s6_passed = true AND s7_passed = true AND s8_passed = true;

-- =====================================================
-- LORA FLOW RESULTS
-- =====================================================

CREATE TABLE flow_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    gap_id VARCHAR(255) NOT NULL REFERENCES gap_reports(gap_id),
    outcome VARCHAR(50) NOT NULL CHECK (outcome IN ('external_adapter', 'self_trained', 'deferred')),
    adapter_id VARCHAR(255),
    security_score DECIMAL(5,4) NOT NULL DEFAULT 0.0 CHECK (security_score >= 0.0 AND security_score <= 1.0),
    votes JSONB DEFAULT '{}',
    warnings JSONB DEFAULT '[]',
    next_evaluation TIMESTAMP WITH TIME ZONE NOT NULL,
    processing_duration_seconds DECIMAL(10,3),
    metadata JSONB DEFAULT '{}',
    completed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for flow results
CREATE INDEX idx_flow_results_gap_id ON flow_results(gap_id);
CREATE INDEX idx_flow_results_outcome ON flow_results(outcome);
CREATE INDEX idx_flow_results_adapter_id ON flow_results(adapter_id);
CREATE INDEX idx_flow_results_completed_at ON flow_results(completed_at DESC);
CREATE INDEX idx_flow_results_next_evaluation ON flow_results(next_evaluation);

-- =====================================================
-- VOTING RECORDS
-- =====================================================

CREATE TABLE voting_records (
    vote_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id VARCHAR(255) NOT NULL,
    gap_id VARCHAR(255) NOT NULL REFERENCES gap_reports(gap_id),
    voter_decisions JSONB NOT NULL DEFAULT '{}',
    final_decision BOOLEAN NOT NULL,
    confidence_score DECIMAL(5,4) NOT NULL DEFAULT 0.0 CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0),
    voting_details JSONB DEFAULT '{}',
    voted_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for voting queries
CREATE INDEX idx_voting_records_candidate_id ON voting_records(candidate_id);
CREATE INDEX idx_voting_records_gap_id ON voting_records(gap_id);
CREATE INDEX idx_voting_records_final_decision ON voting_records(final_decision);
CREATE INDEX idx_voting_records_voted_at ON voting_records(voted_at DESC);
CREATE INDEX idx_voting_records_confidence ON voting_records(confidence_score DESC);

-- =====================================================
-- TRAINING ATTEMPTS
-- =====================================================

CREATE TABLE training_attempts (
    training_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    gap_id VARCHAR(255) NOT NULL REFERENCES gap_reports(gap_id),
    domain VARCHAR(255) NOT NULL,
    training_examples_count INTEGER NOT NULL DEFAULT 0 CHECK (training_examples_count >= 0),
    success BOOLEAN NOT NULL DEFAULT false,
    training_duration_seconds DECIMAL(10,3),
    final_adapter_id VARCHAR(255),
    error_message TEXT,
    training_config JSONB DEFAULT '{}',
    performance_metrics JSONB DEFAULT '{}',
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for training analytics
CREATE INDEX idx_training_attempts_gap_id ON training_attempts(gap_id);
CREATE INDEX idx_training_attempts_domain ON training_attempts(domain);
CREATE INDEX idx_training_attempts_success ON training_attempts(success);
CREATE INDEX idx_training_attempts_started_at ON training_attempts(started_at DESC);
CREATE INDEX idx_training_attempts_domain_success ON training_attempts(domain, success);

-- =====================================================
-- FOREIGN KEY CONSTRAINTS
-- =====================================================

-- Add foreign key constraint for adapter candidates in flow results
-- (nullable, so we can't use NOT NULL constraint)
CREATE INDEX idx_flow_results_adapter_fk ON flow_results(adapter_id) WHERE adapter_id IS NOT NULL;

-- Add foreign key constraint for training attempts final adapter
CREATE INDEX idx_training_attempts_adapter_fk ON training_attempts(final_adapter_id) WHERE final_adapter_id IS NOT NULL;

-- =====================================================
-- PERFORMANCE OPTIMIZATIONS
-- =====================================================

-- Composite indexes for common query patterns
CREATE INDEX idx_gap_reports_pending_by_severity ON gap_reports(status, severity, created_at) WHERE status = 'pending';
CREATE INDEX idx_security_evaluations_gap_candidate ON security_evaluations(gap_id, candidate_id, overall_score DESC);
CREATE INDEX idx_flow_results_outcome_completed ON flow_results(outcome, completed_at DESC);

-- =====================================================
-- DATA RETENTION AND CONSTRAINTS
-- =====================================================

-- Add check constraints for valid score ranges
ALTER TABLE security_evaluations ADD CONSTRAINT check_overall_score_range
    CHECK (overall_score >= 0.0 AND overall_score <= 1.0);
ALTER TABLE flow_results ADD CONSTRAINT check_security_score_range
    CHECK (security_score >= 0.0 AND security_score <= 1.0);
ALTER TABLE voting_records ADD CONSTRAINT check_confidence_score_range
    CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0);

-- Add constraints for parameter counts
ALTER TABLE adapter_candidates ADD CONSTRAINT check_param_count_positive
    CHECK (param_count >= 0);
ALTER TABLE training_attempts ADD CONSTRAINT check_examples_count_non_negative
    CHECK (training_examples_count >= 0);

-- Comments for documentation
COMMENT ON TABLE gap_reports IS 'Capability gaps identified for LoRA adaptation';
COMMENT ON TABLE adapter_candidates IS 'External LoRA candidates fetched from various sources';
COMMENT ON TABLE security_evaluations IS 'Security gate evaluation results (S6, S7, S8)';
COMMENT ON TABLE flow_results IS 'Final outcomes of LoRA orchestration flows';
COMMENT ON TABLE voting_records IS 'Voting decisions for adapter approval';
COMMENT ON TABLE training_attempts IS 'Self-training attempt tracking and metrics';

COMMENT ON COLUMN gap_reports.severity IS 'Priority level: low (deferred), medium/high (processed)';
COMMENT ON COLUMN adapter_candidates.param_count IS 'Number of parameters in the LoRA adapter';
COMMENT ON COLUMN security_evaluations.overall_score IS 'Combined security score (0.0-1.0)';
COMMENT ON COLUMN flow_results.outcome IS 'Final decision: external_adapter, self_trained, or deferred';
COMMENT ON COLUMN voting_records.final_decision IS 'Whether the adapter was approved by voting';
COMMENT ON COLUMN training_attempts.success IS 'Whether self-training completed successfully';