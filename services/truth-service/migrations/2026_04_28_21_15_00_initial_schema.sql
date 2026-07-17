-- Initial truth-service database schema
-- Created: 2026-04-28 21:15:00

-- =====================================================
-- TRUTH FACTS (CORE KNOWLEDGE BASE)
-- =====================================================

CREATE TABLE truth_facts (
    fact_id VARCHAR(255) PRIMARY KEY,
    fact_content TEXT NOT NULL,
    fact_type VARCHAR(100) NOT NULL DEFAULT 'assertion' CHECK (fact_type IN ('assertion', 'rule', 'observation', 'hypothesis', 'conclusion')),
    confidence DECIMAL(5,4) NOT NULL DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    source_id VARCHAR(255),
    source_type VARCHAR(100) NOT NULL DEFAULT 'system' CHECK (source_type IN ('system', 'human', 'proposal', 'correction', 'inference')),
    evidence_hash VARCHAR(128),
    status VARCHAR(50) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deprecated', 'superseded', 'disputed')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    supersedes_fact_id VARCHAR(255),
    deprecation_reason TEXT,
    deprecated_at TIMESTAMP WITH TIME ZONE,
    created_by VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    tags JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}',
    search_vector TSVECTOR
);

-- Indexes for truth facts
CREATE INDEX idx_truth_facts_fact_type ON truth_facts(fact_type);
CREATE INDEX idx_truth_facts_status ON truth_facts(status);
CREATE INDEX idx_truth_facts_confidence ON truth_facts(confidence DESC);
CREATE INDEX idx_truth_facts_source_type ON truth_facts(source_type);
CREATE INDEX idx_truth_facts_created_at ON truth_facts(created_at DESC);
CREATE INDEX idx_truth_facts_created_by ON truth_facts(created_by);
CREATE INDEX idx_truth_facts_supersedes ON truth_facts(supersedes_fact_id) WHERE supersedes_fact_id IS NOT NULL;

-- Full-text search index
CREATE INDEX idx_truth_facts_search ON truth_facts USING GIN(search_vector);

-- Composite indexes for common queries
CREATE INDEX idx_truth_facts_status_type ON truth_facts(status, fact_type, created_at DESC);
CREATE INDEX idx_truth_facts_active_confidence ON truth_facts(confidence DESC, created_at DESC) WHERE status = 'active';

-- =====================================================
-- FACT PROPOSALS (SUBMISSION AND REVIEW SYSTEM)
-- =====================================================

CREATE TABLE fact_proposals (
    proposal_id VARCHAR(255) PRIMARY KEY,
    proposal_type VARCHAR(100) NOT NULL CHECK (proposal_type IN ('new_fact', 'fact_update', 'fact_deprecation', 'fact_correction', 'relationship_addition')),
    status VARCHAR(50) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    priority VARCHAR(20) NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    proposed_fact TEXT NOT NULL,
    target_fact_id VARCHAR(255),
    justification TEXT,
    submitted_by VARCHAR(255) NOT NULL,
    submitted_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE,
    assigned_reviewer VARCHAR(255),
    assigned_at TIMESTAMP WITH TIME ZONE,
    reviewed_by VARCHAR(255),
    reviewed_at TIMESTAMP WITH TIME ZONE,
    review_notes TEXT,
    metadata JSONB DEFAULT '{}'
);

-- Indexes for fact proposals
CREATE INDEX idx_fact_proposals_status ON fact_proposals(status);
CREATE INDEX idx_fact_proposals_proposal_type ON fact_proposals(proposal_type);
CREATE INDEX idx_fact_proposals_priority ON fact_proposals(priority);
CREATE INDEX idx_fact_proposals_submitted_by ON fact_proposals(submitted_by);
CREATE INDEX idx_fact_proposals_submitted_at ON fact_proposals(submitted_at DESC);
CREATE INDEX idx_fact_proposals_assigned_reviewer ON fact_proposals(assigned_reviewer);
CREATE INDEX idx_fact_proposals_target_fact_id ON fact_proposals(target_fact_id) WHERE target_fact_id IS NOT NULL;

-- Composite index for reviewer workload
CREATE INDEX idx_fact_proposals_reviewer_pending ON fact_proposals(assigned_reviewer, priority DESC, submitted_at ASC) WHERE status = 'pending';

-- =====================================================
-- FACT RELATIONSHIPS (KNOWLEDGE GRAPH CONNECTIONS)
-- =====================================================

CREATE TABLE fact_relationships (
    relationship_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_fact_id VARCHAR(255) NOT NULL,
    target_fact_id VARCHAR(255) NOT NULL,
    relationship_type VARCHAR(100) NOT NULL CHECK (relationship_type IN ('depends_on', 'contradicts', 'supports', 'derives_from', 'similar_to', 'generalizes', 'specializes')),
    strength DECIMAL(5,4) NOT NULL DEFAULT 1.0 CHECK (strength >= 0.0 AND strength <= 1.0),
    description TEXT,
    created_by VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    verified BOOLEAN DEFAULT false,
    verified_by VARCHAR(255),
    verified_at TIMESTAMP WITH TIME ZONE
);

-- Indexes for fact relationships
CREATE INDEX idx_fact_relationships_source ON fact_relationships(source_fact_id);
CREATE INDEX idx_fact_relationships_target ON fact_relationships(target_fact_id);
CREATE INDEX idx_fact_relationships_type ON fact_relationships(relationship_type);
CREATE INDEX idx_fact_relationships_strength ON fact_relationships(strength DESC);
CREATE INDEX idx_fact_relationships_created_at ON fact_relationships(created_at DESC);

-- Composite indexes for graph traversal
CREATE INDEX idx_fact_relationships_source_type ON fact_relationships(source_fact_id, relationship_type, strength DESC);
CREATE INDEX idx_fact_relationships_target_type ON fact_relationships(target_fact_id, relationship_type, strength DESC);

-- Unique constraint to prevent duplicate relationships
CREATE UNIQUE INDEX idx_fact_relationships_unique ON fact_relationships(source_fact_id, target_fact_id, relationship_type);

-- =====================================================
-- FACT EVIDENCE (SUPPORTING DOCUMENTATION)
-- =====================================================

CREATE TABLE fact_evidence (
    evidence_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fact_id VARCHAR(255) NOT NULL,
    evidence_type VARCHAR(100) NOT NULL CHECK (evidence_type IN ('citation', 'document', 'experiment', 'observation', 'testimony', 'analysis')),
    evidence_content TEXT NOT NULL,
    evidence_url VARCHAR(2048),
    credibility_score DECIMAL(5,4) DEFAULT 1.0 CHECK (credibility_score >= 0.0 AND credibility_score <= 1.0),
    added_by VARCHAR(255) NOT NULL,
    added_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    verified BOOLEAN DEFAULT false,
    verified_by VARCHAR(255),
    verified_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}'
);

-- Indexes for fact evidence
CREATE INDEX idx_fact_evidence_fact_id ON fact_evidence(fact_id);
CREATE INDEX idx_fact_evidence_evidence_type ON fact_evidence(evidence_type);
CREATE INDEX idx_fact_evidence_credibility ON fact_evidence(credibility_score DESC);
CREATE INDEX idx_fact_evidence_added_at ON fact_evidence(added_at DESC);
CREATE INDEX idx_fact_evidence_verified ON fact_evidence(verified);

-- Composite index for fact evidence quality
CREATE INDEX idx_fact_evidence_fact_credibility ON fact_evidence(fact_id, credibility_score DESC, added_at DESC);

-- =====================================================
-- FACT VERSIONS (CHANGE HISTORY)
-- =====================================================

CREATE TABLE fact_versions (
    version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fact_id VARCHAR(255) NOT NULL,
    version_number INTEGER NOT NULL,
    changes JSONB NOT NULL,
    changed_by VARCHAR(255) NOT NULL,
    changed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    change_reason TEXT,
    previous_version_id UUID
);

-- Indexes for fact versions
CREATE INDEX idx_fact_versions_fact_id ON fact_versions(fact_id);
CREATE INDEX idx_fact_versions_version_number ON fact_versions(version_number DESC);
CREATE INDEX idx_fact_versions_changed_at ON fact_versions(changed_at DESC);
CREATE INDEX idx_fact_versions_changed_by ON fact_versions(changed_by);

-- Composite index for fact history
CREATE INDEX idx_fact_versions_fact_history ON fact_versions(fact_id, version_number DESC);

-- Unique constraint for fact-version combinations
CREATE UNIQUE INDEX idx_fact_versions_unique ON fact_versions(fact_id, version_number);

-- =====================================================
-- TRUTH METRICS (QUALITY AND ANALYTICS)
-- =====================================================

CREATE TABLE truth_metrics (
    metric_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    metric_type VARCHAR(100) NOT NULL CHECK (metric_type IN ('fact_quality', 'source_reliability', 'consensus_level', 'evidence_strength')),
    target_id VARCHAR(255) NOT NULL,
    target_type VARCHAR(100) NOT NULL CHECK (target_type IN ('fact', 'source', 'evidence', 'relationship')),
    metric_value DECIMAL(10,6) NOT NULL,
    confidence_interval DECIMAL(5,4),
    computed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    computed_by VARCHAR(255),
    computation_method TEXT,
    metadata JSONB DEFAULT '{}'
);

-- Indexes for truth metrics
CREATE INDEX idx_truth_metrics_metric_type ON truth_metrics(metric_type);
CREATE INDEX idx_truth_metrics_target ON truth_metrics(target_type, target_id);
CREATE INDEX idx_truth_metrics_value ON truth_metrics(metric_value DESC);
CREATE INDEX idx_truth_metrics_computed_at ON truth_metrics(computed_at DESC);

-- =====================================================
-- CONSENSUS TRACKING (AGREEMENT/DISAGREEMENT)
-- =====================================================

CREATE TABLE fact_consensus (
    consensus_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fact_id VARCHAR(255) NOT NULL,
    participant_id VARCHAR(255) NOT NULL,
    consensus_type VARCHAR(50) NOT NULL CHECK (consensus_type IN ('agree', 'disagree', 'uncertain', 'partial')),
    confidence DECIMAL(5,4) DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    reasoning TEXT,
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE,
    metadata JSONB DEFAULT '{}'
);

-- Indexes for consensus tracking
CREATE INDEX idx_fact_consensus_fact_id ON fact_consensus(fact_id);
CREATE INDEX idx_fact_consensus_participant ON fact_consensus(participant_id);
CREATE INDEX idx_fact_consensus_type ON fact_consensus(consensus_type);
CREATE INDEX idx_fact_consensus_recorded_at ON fact_consensus(recorded_at DESC);

-- Unique constraint for participant-fact consensus
CREATE UNIQUE INDEX idx_fact_consensus_unique ON fact_consensus(fact_id, participant_id);

-- =====================================================
-- FOREIGN KEY CONSTRAINTS
-- =====================================================

-- Link proposals to target facts
ALTER TABLE fact_proposals
ADD CONSTRAINT fk_fact_proposals_target_fact_id
FOREIGN KEY (target_fact_id) REFERENCES truth_facts(fact_id)
ON DELETE SET NULL;

-- Link relationships to source and target facts
ALTER TABLE fact_relationships
ADD CONSTRAINT fk_fact_relationships_source_fact_id
FOREIGN KEY (source_fact_id) REFERENCES truth_facts(fact_id)
ON DELETE CASCADE;

ALTER TABLE fact_relationships
ADD CONSTRAINT fk_fact_relationships_target_fact_id
FOREIGN KEY (target_fact_id) REFERENCES truth_facts(fact_id)
ON DELETE CASCADE;

-- Link evidence to facts
ALTER TABLE fact_evidence
ADD CONSTRAINT fk_fact_evidence_fact_id
FOREIGN KEY (fact_id) REFERENCES truth_facts(fact_id)
ON DELETE CASCADE;

-- Link versions to facts
ALTER TABLE fact_versions
ADD CONSTRAINT fk_fact_versions_fact_id
FOREIGN KEY (fact_id) REFERENCES truth_facts(fact_id)
ON DELETE CASCADE;

-- Link consensus to facts
ALTER TABLE fact_consensus
ADD CONSTRAINT fk_fact_consensus_fact_id
FOREIGN KEY (fact_id) REFERENCES truth_facts(fact_id)
ON DELETE CASCADE;

-- Link superseding facts
ALTER TABLE truth_facts
ADD CONSTRAINT fk_truth_facts_supersedes_fact_id
FOREIGN KEY (supersedes_fact_id) REFERENCES truth_facts(fact_id)
ON DELETE SET NULL;

-- =====================================================
-- TRIGGERS FOR AUTOMATIC UPDATES
-- =====================================================

-- Update updated_at timestamp on truth facts
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_truth_facts_updated_at
    BEFORE UPDATE ON truth_facts
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Automatically update search vector when fact content changes
CREATE OR REPLACE FUNCTION update_fact_search_vector()
RETURNS TRIGGER AS $$
BEGIN
    NEW.search_vector = to_tsvector('english', NEW.fact_content || ' ' || COALESCE(NEW.tags::text, ''));
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_truth_facts_search_vector
    BEFORE INSERT OR UPDATE OF fact_content, tags ON truth_facts
    FOR EACH ROW
    EXECUTE FUNCTION update_fact_search_vector();

-- Create fact version record on significant changes
CREATE OR REPLACE FUNCTION create_fact_version()
RETURNS TRIGGER AS $$
BEGIN
    -- Only create version for significant changes
    IF OLD.fact_content != NEW.fact_content OR OLD.confidence != NEW.confidence OR OLD.status != NEW.status THEN
        INSERT INTO fact_versions (fact_id, version_number, changes, changed_by)
        VALUES (
            NEW.fact_id,
            NEW.version,
            jsonb_build_object(
                'fact_content', jsonb_build_object('old', OLD.fact_content, 'new', NEW.fact_content),
                'confidence', jsonb_build_object('old', OLD.confidence, 'new', NEW.confidence),
                'status', jsonb_build_object('old', OLD.status, 'new', NEW.status)
            ),
            COALESCE(NEW.created_by, 'system')
        );
    END IF;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER create_truth_facts_version
    AFTER UPDATE ON truth_facts
    FOR EACH ROW
    EXECUTE FUNCTION create_fact_version();

-- =====================================================
-- DATA INTEGRITY CONSTRAINTS
-- =====================================================

-- Ensure fact confidence is within valid range
ALTER TABLE truth_facts ADD CONSTRAINT check_fact_confidence_range
CHECK (confidence >= 0.0 AND confidence <= 1.0);

-- Ensure relationship strength is within valid range
ALTER TABLE fact_relationships ADD CONSTRAINT check_relationship_strength_range
CHECK (strength >= 0.0 AND strength <= 1.0);

-- Ensure evidence credibility is within valid range
ALTER TABLE fact_evidence ADD CONSTRAINT check_evidence_credibility_range
CHECK (credibility_score >= 0.0 AND credibility_score <= 1.0);

-- Ensure consensus confidence is within valid range
ALTER TABLE fact_consensus ADD CONSTRAINT check_consensus_confidence_range
CHECK (confidence >= 0.0 AND confidence <= 1.0);

-- Ensure version numbers are positive
ALTER TABLE fact_versions ADD CONSTRAINT check_version_number_positive
CHECK (version_number > 0);

-- Ensure proposal expiration is in the future when set
ALTER TABLE fact_proposals ADD CONSTRAINT check_proposal_expiration_future
CHECK (expires_at IS NULL OR expires_at > submitted_at);

-- Prevent self-referencing fact relationships
ALTER TABLE fact_relationships ADD CONSTRAINT check_no_self_relationship
CHECK (source_fact_id != target_fact_id);

-- Ensure superseding fact is different from original
ALTER TABLE truth_facts ADD CONSTRAINT check_supersedes_not_self
CHECK (supersedes_fact_id IS NULL OR supersedes_fact_id != fact_id);

-- =====================================================
-- PERFORMANCE OPTIMIZATIONS
-- =====================================================

-- Partial indexes for active content
CREATE INDEX idx_truth_facts_active_content ON truth_facts(fact_type, confidence DESC, created_at DESC)
WHERE status = 'active';

CREATE INDEX idx_fact_proposals_pending ON fact_proposals(priority DESC, submitted_at ASC)
WHERE status = 'pending';

-- Index for consensus analysis
CREATE INDEX idx_fact_consensus_agreement ON fact_consensus(fact_id, consensus_type)
WHERE consensus_type IN ('agree', 'disagree');

-- Index for evidence quality analysis
CREATE INDEX idx_fact_evidence_high_credibility ON fact_evidence(fact_id, added_at DESC)
WHERE credibility_score >= 0.8;

-- =====================================================
-- DOCUMENTATION COMMENTS
-- =====================================================

COMMENT ON TABLE truth_facts IS 'Core knowledge base storing validated facts with confidence scores and versioning';
COMMENT ON TABLE fact_proposals IS 'Submission and review system for proposed fact changes';
COMMENT ON TABLE fact_relationships IS 'Knowledge graph relationships between facts';
COMMENT ON TABLE fact_evidence IS 'Supporting evidence and citations for facts';
COMMENT ON TABLE fact_versions IS 'Version history tracking fact changes over time';
COMMENT ON TABLE truth_metrics IS 'Computed quality and reliability metrics';
COMMENT ON TABLE fact_consensus IS 'Participant agreement/disagreement tracking';

COMMENT ON COLUMN truth_facts.confidence IS 'Confidence score for fact accuracy (0.0-1.0)';
COMMENT ON COLUMN truth_facts.evidence_hash IS 'Hash of supporting evidence for integrity verification';
COMMENT ON COLUMN truth_facts.search_vector IS 'Full-text search vector for efficient content search';
COMMENT ON COLUMN fact_proposals.priority IS 'Review priority: low, normal, high, urgent';
COMMENT ON COLUMN fact_relationships.strength IS 'Relationship strength/certainty (0.0-1.0)';
COMMENT ON COLUMN fact_evidence.credibility_score IS 'Evidence source credibility (0.0-1.0)';
COMMENT ON COLUMN fact_versions.changes IS 'JSON object describing what changed in this version';
COMMENT ON COLUMN truth_metrics.metric_value IS 'Computed metric value (interpretation depends on metric_type)';
COMMENT ON COLUMN fact_consensus.consensus_type IS 'Type of consensus: agree, disagree, uncertain, partial';