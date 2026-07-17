-- Initial provenance-service database schema
-- Created: 2026-04-28 20:45:00

-- =====================================================
-- PROVENANCE EDGES (CORE GRAPH DATA)
-- =====================================================

CREATE TABLE provenance_edges (
    edge_id BIGSERIAL PRIMARY KEY,
    edge_hash VARCHAR(64) UNIQUE NOT NULL,
    src_type VARCHAR(100) NOT NULL,
    src_id VARCHAR(256) NOT NULL,
    dst_type VARCHAR(100) NOT NULL,
    dst_id VARCHAR(256) NOT NULL,
    ts TIMESTAMP WITH TIME ZONE NOT NULL,
    meta_json JSONB DEFAULT '{}',
    confidence DECIMAL(5,4) DEFAULT 1.0 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    validation_status VARCHAR(50) DEFAULT 'unvalidated' CHECK (validation_status IN ('unvalidated', 'valid', 'invalid', 'suspicious')),
    created_by VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for efficient graph traversal
CREATE INDEX idx_provenance_edges_src ON provenance_edges(src_type, src_id);
CREATE INDEX idx_provenance_edges_dst ON provenance_edges(dst_type, dst_id);
CREATE INDEX idx_provenance_edges_ts ON provenance_edges(ts DESC);
CREATE INDEX idx_provenance_edges_created_at ON provenance_edges(created_at DESC);
CREATE INDEX idx_provenance_edges_confidence ON provenance_edges(confidence DESC);
CREATE INDEX idx_provenance_edges_validation_status ON provenance_edges(validation_status);

-- Composite indexes for common traversal patterns
CREATE INDEX idx_provenance_edges_src_ts ON provenance_edges(src_type, src_id, ts DESC);
CREATE INDEX idx_provenance_edges_dst_ts ON provenance_edges(dst_type, dst_id, ts DESC);
CREATE INDEX idx_provenance_edges_bidirectional ON provenance_edges(src_type, src_id, dst_type, dst_id);

-- =====================================================
-- LINEAGE CACHE (PERFORMANCE OPTIMIZATION)
-- =====================================================

CREATE TABLE lineage_cache (
    cache_key VARCHAR(255) PRIMARY KEY,
    target_type VARCHAR(100) NOT NULL,
    target_id VARCHAR(256) NOT NULL,
    query_type VARCHAR(50) NOT NULL CHECK (query_type IN ('upstream', 'downstream', 'both')),
    result_json JSONB NOT NULL,
    computed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    hit_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for cache management
CREATE INDEX idx_lineage_cache_target ON lineage_cache(target_type, target_id);
CREATE INDEX idx_lineage_cache_expires_at ON lineage_cache(expires_at);
CREATE INDEX idx_lineage_cache_query_type ON lineage_cache(query_type);
CREATE INDEX idx_lineage_cache_computed_at ON lineage_cache(computed_at DESC);

-- =====================================================
-- GRAPH STATISTICS (ANALYTICS)
-- =====================================================

CREATE TABLE graph_statistics (
    stat_id BIGSERIAL PRIMARY KEY,
    computed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    total_nodes INTEGER DEFAULT 0,
    total_edges INTEGER DEFAULT 0,
    max_depth INTEGER,
    connected_components INTEGER,
    cycles_detected INTEGER DEFAULT 0,
    entity_type_distribution_json JSONB DEFAULT '{}',
    temporal_span_days DECIMAL(10,2),
    confidence_stats_json JSONB DEFAULT '{}',
    computation_duration_ms INTEGER
);

-- Index for retrieving recent statistics
CREATE INDEX idx_graph_statistics_computed_at ON graph_statistics(computed_at DESC);

-- =====================================================
-- GRAPH VALIDATION RESULTS
-- =====================================================

CREATE TABLE graph_validation_results (
    validation_id BIGSERIAL PRIMARY KEY,
    validated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    validation_type VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL CHECK (status IN ('passed', 'failed', 'warning')),
    issues_found INTEGER DEFAULT 0,
    issues_json JSONB DEFAULT '[]',
    validation_duration_ms INTEGER,
    validator_version VARCHAR(50),
    validation_config_json JSONB DEFAULT '{}'
);

-- Indexes for validation queries
CREATE INDEX idx_graph_validation_results_validated_at ON graph_validation_results(validated_at DESC);
CREATE INDEX idx_graph_validation_results_status ON graph_validation_results(status);
CREATE INDEX idx_graph_validation_results_type ON graph_validation_results(validation_type);

-- =====================================================
-- PROVENANCE AUDIT LOG
-- =====================================================

CREATE TABLE provenance_audit_log (
    audit_id BIGSERIAL PRIMARY KEY,
    operation VARCHAR(100) NOT NULL,
    target_id VARCHAR(256),
    operation_data_json JSONB DEFAULT '{}',
    performed_by VARCHAR(255),
    performed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    correlation_id VARCHAR(255),
    source_service VARCHAR(100),
    request_id VARCHAR(255),
    client_ip INET,
    user_agent TEXT
);

-- Indexes for audit queries
CREATE INDEX idx_provenance_audit_log_performed_at ON provenance_audit_log(performed_at DESC);
CREATE INDEX idx_provenance_audit_log_operation ON provenance_audit_log(operation);
CREATE INDEX idx_provenance_audit_log_correlation_id ON provenance_audit_log(correlation_id);
CREATE INDEX idx_provenance_audit_log_source_service ON provenance_audit_log(source_service);
CREATE INDEX idx_provenance_audit_log_target_id ON provenance_audit_log(target_id);

-- =====================================================
-- MIGRATION TRACKING
-- =====================================================

CREATE TABLE provenance_migration_log (
    migration_id VARCHAR(255) NOT NULL,
    operation VARCHAR(100) NOT NULL,
    records_processed INTEGER DEFAULT 0,
    status VARCHAR(50) NOT NULL CHECK (status IN ('started', 'in_progress', 'completed', 'failed', 'rolled_back')),
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    error_message TEXT,
    migration_data_json JSONB DEFAULT '{}',
    source_location VARCHAR(1024),
    target_location VARCHAR(1024)
);

-- Index for migration tracking
CREATE INDEX idx_provenance_migration_log_migration_id ON provenance_migration_log(migration_id);
CREATE INDEX idx_provenance_migration_log_started_at ON provenance_migration_log(started_at DESC);
CREATE INDEX idx_provenance_migration_log_status ON provenance_migration_log(status);

-- =====================================================
-- ENTITY METADATA (OPTIONAL ENHANCEMENT)
-- =====================================================

CREATE TABLE entity_metadata (
    entity_type VARCHAR(100) NOT NULL,
    entity_id VARCHAR(256) NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(255),
    PRIMARY KEY (entity_type, entity_id)
);

-- Index for entity metadata queries
CREATE INDEX idx_entity_metadata_created_at ON entity_metadata(created_at DESC);
CREATE INDEX idx_entity_metadata_type ON entity_metadata(entity_type);

-- =====================================================
-- PERFORMANCE OPTIMIZATIONS
-- =====================================================

-- Materialized view for frequently accessed entity counts (optional)
-- CREATE MATERIALIZED VIEW entity_type_counts AS
-- SELECT
--     entity_type,
--     COUNT(DISTINCT entity_id) as count
-- FROM (
--     SELECT src_type as entity_type, src_id as entity_id FROM provenance_edges
--     UNION
--     SELECT dst_type as entity_type, dst_id as entity_id FROM provenance_edges
-- ) entities
-- GROUP BY entity_type;
--
-- CREATE UNIQUE INDEX idx_entity_type_counts_type ON entity_type_counts(entity_type);

-- Partial indexes for common queries
CREATE INDEX idx_provenance_edges_recent_high_confidence ON provenance_edges(created_at DESC)
WHERE confidence >= 0.8;

CREATE INDEX idx_provenance_edges_validated ON provenance_edges(src_type, src_id, ts DESC)
WHERE validation_status = 'valid';

-- =====================================================
-- DATA INTEGRITY CONSTRAINTS
-- =====================================================

-- Ensure edge hash uniqueness
ALTER TABLE provenance_edges ADD CONSTRAINT unique_edge_hash UNIQUE (edge_hash);

-- Ensure non-empty entity identifiers
ALTER TABLE provenance_edges ADD CONSTRAINT check_src_id_not_empty CHECK (LENGTH(TRIM(src_id)) > 0);
ALTER TABLE provenance_edges ADD CONSTRAINT check_dst_id_not_empty CHECK (LENGTH(TRIM(dst_id)) > 0);
ALTER TABLE provenance_edges ADD CONSTRAINT check_src_type_not_empty CHECK (LENGTH(TRIM(src_type)) > 0);
ALTER TABLE provenance_edges ADD CONSTRAINT check_dst_type_not_empty CHECK (LENGTH(TRIM(dst_type)) > 0);

-- Ensure confidence is within valid range
ALTER TABLE provenance_edges ADD CONSTRAINT check_confidence_range CHECK (confidence >= 0.0 AND confidence <= 1.0);

-- Ensure positive values where appropriate
ALTER TABLE graph_statistics ADD CONSTRAINT check_total_nodes_non_negative CHECK (total_nodes >= 0);
ALTER TABLE graph_statistics ADD CONSTRAINT check_total_edges_non_negative CHECK (total_edges >= 0);
ALTER TABLE graph_validation_results ADD CONSTRAINT check_issues_found_non_negative CHECK (issues_found >= 0);
ALTER TABLE lineage_cache ADD CONSTRAINT check_hit_count_non_negative CHECK (hit_count >= 0);

-- Ensure cache expiration is in the future when created
ALTER TABLE lineage_cache ADD CONSTRAINT check_expires_after_computed CHECK (expires_at > computed_at);

-- =====================================================
-- TRIGGERS FOR AUTOMATIC UPDATES
-- =====================================================

-- Update updated_at timestamp on provenance_edges
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_provenance_edges_updated_at
    BEFORE UPDATE ON provenance_edges
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Update last_accessed on cache hits
CREATE OR REPLACE FUNCTION update_cache_access()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_accessed = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_lineage_cache_access
    BEFORE UPDATE ON lineage_cache
    FOR EACH ROW
    WHEN (OLD.hit_count IS DISTINCT FROM NEW.hit_count)
    EXECUTE FUNCTION update_cache_access();

-- =====================================================
-- DOCUMENTATION COMMENTS
-- =====================================================

COMMENT ON TABLE provenance_edges IS 'Core provenance graph edges linking entities with metadata and confidence scores';
COMMENT ON TABLE lineage_cache IS 'Cached lineage query results for performance optimization';
COMMENT ON TABLE graph_statistics IS 'Computed graph analytics and structure statistics';
COMMENT ON TABLE graph_validation_results IS 'Results from graph integrity and validation checks';
COMMENT ON TABLE provenance_audit_log IS 'Audit trail for all provenance graph operations';
COMMENT ON TABLE provenance_migration_log IS 'Tracking log for database migration operations';
COMMENT ON TABLE entity_metadata IS 'Optional metadata storage for graph entities';

COMMENT ON COLUMN provenance_edges.edge_hash IS 'Unique hash identifying this specific edge (src+dst+timestamp)';
COMMENT ON COLUMN provenance_edges.confidence IS 'Confidence score for this provenance link (0.0-1.0)';
COMMENT ON COLUMN provenance_edges.validation_status IS 'Validation state: unvalidated, valid, invalid, suspicious';
COMMENT ON COLUMN lineage_cache.query_type IS 'Type of lineage query: upstream, downstream, or both';
COMMENT ON COLUMN lineage_cache.hit_count IS 'Number of times this cache entry has been accessed';
COMMENT ON COLUMN graph_statistics.connected_components IS 'Number of disconnected graph components';
COMMENT ON COLUMN graph_validation_results.issues_found IS 'Count of validation issues discovered';
COMMENT ON COLUMN provenance_audit_log.correlation_id IS 'Request correlation ID for tracing operations';
COMMENT ON COLUMN provenance_migration_log.migration_id IS 'Unique identifier for migration batch';