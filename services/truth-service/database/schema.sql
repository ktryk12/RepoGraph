-- Truth Service Database Schema
-- ADR-0015: Phase 2 Database-per-Service
-- Consolidates facts.sqlite + proposals.sqlite from shared locations

-- Enable foreign key constraints
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;  -- Write-Ahead Logging for better concurrency

-- Truth Facts Table
-- Consolidates facts from multiple source databases
CREATE TABLE truth_facts (
    -- Primary key and identity
    fact_id TEXT PRIMARY KEY,              -- UUID for fact identity

    -- Content
    fact_content TEXT NOT NULL,           -- The actual truth assertion
    fact_type TEXT NOT NULL,              -- Type: assertion, rule, observation, etc.
    confidence REAL DEFAULT 1.0,          -- Confidence level 0.0-1.0

    -- Provenance
    source_id TEXT,                       -- Where this fact originated
    source_type TEXT,                     -- Source type: agent, human, import, etc.
    evidence_hash TEXT,                   -- Hash of supporting evidence

    -- Lifecycle
    status TEXT DEFAULT 'active',         -- active, deprecated, superseded, disputed
    version INTEGER DEFAULT 1,            -- Fact version number
    supersedes_fact_id TEXT,              -- ID of fact this supersedes

    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT,
    tags TEXT,                            -- JSON array of tags
    metadata TEXT,                        -- JSON metadata

    -- Migration tracking
    migrated_from TEXT,                   -- Source database file
    original_id TEXT,                     -- Original fact ID in source database

    FOREIGN KEY (supersedes_fact_id) REFERENCES truth_facts(fact_id)
);

-- Truth Proposals Table
-- Consolidates proposals from multiple source databases
CREATE TABLE truth_proposals (
    -- Primary key and identity
    proposal_id TEXT PRIMARY KEY,         -- UUID for proposal identity

    -- Content
    proposed_fact TEXT NOT NULL,          -- The proposed truth assertion
    proposal_type TEXT NOT NULL,          -- Type: new_fact, fact_update, fact_deprecation
    justification TEXT,                   -- Why this proposal should be accepted
    evidence_data TEXT,                   -- JSON evidence supporting proposal

    -- Target (for updates/deprecations)
    target_fact_id TEXT,                  -- Fact this proposal relates to

    -- Lifecycle
    status TEXT DEFAULT 'pending',        -- pending, reviewing, approved, rejected, expired
    priority TEXT DEFAULT 'normal',       -- low, normal, high, critical

    -- Review process
    submitted_by TEXT NOT NULL,           -- Who submitted the proposal
    reviewer_id TEXT,                     -- Who is reviewing/reviewed
    review_notes TEXT,                    -- Reviewer notes
    approval_reason TEXT,                 -- Why approved/rejected

    -- Timing
    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    review_started_at TIMESTAMP,
    completed_at TIMESTAMP,
    expires_at TIMESTAMP,                 -- When proposal expires if not acted upon

    -- Metadata
    metadata TEXT,                        -- JSON metadata
    tags TEXT,                           -- JSON array of tags

    -- Migration tracking
    migrated_from TEXT,                   -- Source database file
    original_id TEXT,                     -- Original proposal ID in source database

    FOREIGN KEY (target_fact_id) REFERENCES truth_facts(fact_id)
);

-- Truth Relationships Table
-- Tracks relationships between facts
CREATE TABLE truth_relationships (
    relationship_id TEXT PRIMARY KEY,     -- UUID for relationship

    -- Relationship definition
    source_fact_id TEXT NOT NULL,        -- Source fact
    target_fact_id TEXT NOT NULL,        -- Target fact
    relationship_type TEXT NOT NULL,     -- depends_on, contradicts, supports, derives_from
    strength REAL DEFAULT 1.0,           -- Relationship strength 0.0-1.0

    -- Metadata
    description TEXT,                     -- Human readable description
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT,
    metadata TEXT,                       -- JSON metadata

    FOREIGN KEY (source_fact_id) REFERENCES truth_facts(fact_id),
    FOREIGN KEY (target_fact_id) REFERENCES truth_facts(fact_id),

    -- Prevent circular relationships
    CONSTRAINT no_self_reference CHECK (source_fact_id != target_fact_id)
);

-- Truth Versions Table
-- Tracks change history for facts
CREATE TABLE truth_versions (
    version_id TEXT PRIMARY KEY,          -- UUID for version
    fact_id TEXT NOT NULL,               -- Which fact this version belongs to
    version_number INTEGER NOT NULL,      -- Version number (1, 2, 3...)

    -- Version content
    content_snapshot TEXT NOT NULL,       -- Complete fact state at this version
    change_type TEXT NOT NULL,           -- created, updated, deprecated, superseded
    change_description TEXT,             -- What changed

    -- Change metadata
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    changed_by TEXT,
    change_reason TEXT,                  -- Why the change was made

    FOREIGN KEY (fact_id) REFERENCES truth_facts(fact_id),
    UNIQUE (fact_id, version_number)
);

-- Query Cache Table (for performance)
CREATE TABLE truth_query_cache (
    query_hash TEXT PRIMARY KEY,         -- Hash of query parameters
    query_text TEXT NOT NULL,           -- Original query
    result_data TEXT NOT NULL,          -- JSON result

    -- Cache management
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,       -- When cache entry expires
    access_count INTEGER DEFAULT 1,      -- How often accessed
    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Migration Log Table
CREATE TABLE truth_migration_log (
    migration_id TEXT PRIMARY KEY,       -- UUID for migration operation

    -- Migration details
    source_database TEXT NOT NULL,       -- Source file path
    migration_type TEXT NOT NULL,       -- facts, proposals, full

    -- Progress tracking
    status TEXT DEFAULT 'started',       -- started, in_progress, completed, failed
    records_processed INTEGER DEFAULT 0,
    records_migrated INTEGER DEFAULT 0,
    records_skipped INTEGER DEFAULT 0,
    errors_encountered INTEGER DEFAULT 0,

    -- Timing
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,

    -- Details
    error_details TEXT,                  -- JSON error information
    migration_notes TEXT,               -- Notes about migration process

    -- Validation
    data_validation_status TEXT,        -- passed, failed, pending
    integrity_check_results TEXT        -- JSON validation results
);

-- Indexes for performance
CREATE INDEX idx_truth_facts_status ON truth_facts(status);
CREATE INDEX idx_truth_facts_created_at ON truth_facts(created_at);
CREATE INDEX idx_truth_facts_fact_type ON truth_facts(fact_type);
CREATE INDEX idx_truth_facts_source_id ON truth_facts(source_id);
CREATE INDEX idx_truth_facts_migrated_from ON truth_facts(migrated_from);

CREATE INDEX idx_truth_proposals_status ON truth_proposals(status);
CREATE INDEX idx_truth_proposals_submitted_at ON truth_proposals(submitted_at);
CREATE INDEX idx_truth_proposals_submitted_by ON truth_proposals(submitted_by);
CREATE INDEX idx_truth_proposals_target_fact_id ON truth_proposals(target_fact_id);
CREATE INDEX idx_truth_proposals_migrated_from ON truth_proposals(migrated_from);

CREATE INDEX idx_truth_relationships_source ON truth_relationships(source_fact_id);
CREATE INDEX idx_truth_relationships_target ON truth_relationships(target_fact_id);
CREATE INDEX idx_truth_relationships_type ON truth_relationships(relationship_type);

CREATE INDEX idx_truth_versions_fact_id ON truth_versions(fact_id);
CREATE INDEX idx_truth_versions_changed_at ON truth_versions(changed_at);

CREATE INDEX idx_truth_query_cache_expires ON truth_query_cache(expires_at);

-- Triggers for maintaining data consistency

-- Update truth_facts.updated_at on changes
CREATE TRIGGER truth_facts_updated_at
    AFTER UPDATE ON truth_facts
    BEGIN
        UPDATE truth_facts
        SET updated_at = CURRENT_TIMESTAMP
        WHERE fact_id = NEW.fact_id;
    END;

-- Create version record when fact is created or updated
CREATE TRIGGER truth_facts_version_tracking
    AFTER INSERT ON truth_facts
    BEGIN
        INSERT INTO truth_versions (
            version_id, fact_id, version_number, content_snapshot,
            change_type, changed_by, change_description
        ) VALUES (
            NEW.fact_id || '_v' || NEW.version,
            NEW.fact_id,
            NEW.version,
            json_object(
                'fact_content', NEW.fact_content,
                'fact_type', NEW.fact_type,
                'confidence', NEW.confidence,
                'status', NEW.status
            ),
            'created',
            NEW.created_by,
            'Initial fact creation'
        );
    END;

-- Update version record when fact is updated
CREATE TRIGGER truth_facts_version_update
    AFTER UPDATE ON truth_facts
    WHEN NEW.version > OLD.version
    BEGIN
        INSERT INTO truth_versions (
            version_id, fact_id, version_number, content_snapshot,
            change_type, changed_by, change_description
        ) VALUES (
            NEW.fact_id || '_v' || NEW.version,
            NEW.fact_id,
            NEW.version,
            json_object(
                'fact_content', NEW.fact_content,
                'fact_type', NEW.fact_type,
                'confidence', NEW.confidence,
                'status', NEW.status
            ),
            'updated',
            NEW.created_by,
            'Fact updated'
        );
    END;

-- Clean up expired query cache entries
CREATE TRIGGER truth_query_cache_cleanup
    AFTER INSERT ON truth_query_cache
    BEGIN
        DELETE FROM truth_query_cache
        WHERE expires_at < CURRENT_TIMESTAMP;
    END;

-- Views for common queries

-- Active facts view
CREATE VIEW active_facts AS
SELECT
    fact_id, fact_content, fact_type, confidence,
    source_id, source_type, created_at, created_by, tags
FROM truth_facts
WHERE status = 'active';

-- Pending proposals view
CREATE VIEW pending_proposals AS
SELECT
    proposal_id, proposed_fact, proposal_type, justification,
    submitted_by, submitted_at, priority, expires_at
FROM truth_proposals
WHERE status = 'pending'
    AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP);

-- Migration status view
CREATE VIEW migration_status AS
SELECT
    source_database,
    status,
    records_migrated,
    records_processed,
    CASE
        WHEN records_processed > 0 THEN
            ROUND(100.0 * records_migrated / records_processed, 2)
        ELSE 0
    END as migration_percentage,
    started_at,
    completed_at
FROM truth_migration_log
ORDER BY started_at DESC;