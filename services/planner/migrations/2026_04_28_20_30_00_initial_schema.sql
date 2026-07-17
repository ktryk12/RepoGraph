-- Initial planner database schema
-- Created: 2026-04-28 20:30:00

-- =====================================================
-- INTENT RECORDS
-- =====================================================

CREATE TABLE intent_records (
    decision_id VARCHAR(255) PRIMARY KEY,
    context_id VARCHAR(255) NOT NULL DEFAULT 'dev',
    policy_preset VARCHAR(50) NOT NULL DEFAULT 'dev' CHECK (policy_preset IN ('public', 'dev', 'restricted')),
    user_prompt TEXT NOT NULL,
    template_id VARCHAR(100) NOT NULL DEFAULT 'auto',
    metadata JSONB DEFAULT '{}',
    received_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for intent queries
CREATE INDEX idx_intent_records_context_id ON intent_records(context_id);
CREATE INDEX idx_intent_records_policy_preset ON intent_records(policy_preset);
CREATE INDEX idx_intent_records_template_id ON intent_records(template_id);
CREATE INDEX idx_intent_records_received_at ON intent_records(received_at DESC);

-- =====================================================
-- READY RECORDS
-- =====================================================

CREATE TABLE ready_records (
    decision_id VARCHAR(255) PRIMARY KEY,
    context_id VARCHAR(255) NOT NULL DEFAULT 'dev',
    policy_preset VARCHAR(50) NOT NULL DEFAULT 'dev' CHECK (policy_preset IN ('public', 'dev', 'restricted')),
    truth_pack_alias VARCHAR(255) NOT NULL DEFAULT 'layered_default',
    user_override_ref VARCHAR(1024) NOT NULL,
    explanation_text TEXT NOT NULL,
    override_hash VARCHAR(128) NOT NULL,
    metadata JSONB DEFAULT '{}',
    received_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for ready record queries
CREATE INDEX idx_ready_records_context_id ON ready_records(context_id);
CREATE INDEX idx_ready_records_truth_pack_alias ON ready_records(truth_pack_alias);
CREATE INDEX idx_ready_records_override_hash ON ready_records(override_hash);
CREATE INDEX idx_ready_records_received_at ON ready_records(received_at DESC);

-- =====================================================
-- TASK SPECIFICATIONS
-- =====================================================

CREATE TABLE task_specifications (
    task_id VARCHAR(255) PRIMARY KEY,
    decision_id VARCHAR(255) NOT NULL,
    task_spec JSONB NOT NULL,
    task_ref VARCHAR(1024) NOT NULL,
    template_id VARCHAR(100) NOT NULL DEFAULT 'auto',
    context_id VARCHAR(255),
    policy_preset VARCHAR(50) CHECK (policy_preset IN ('public', 'dev', 'restricted')),
    generated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for task specification queries
CREATE INDEX idx_task_specifications_decision_id ON task_specifications(decision_id);
CREATE INDEX idx_task_specifications_template_id ON task_specifications(template_id);
CREATE INDEX idx_task_specifications_context_id ON task_specifications(context_id);
CREATE INDEX idx_task_specifications_policy_preset ON task_specifications(policy_preset);
CREATE INDEX idx_task_specifications_generated_at ON task_specifications(generated_at DESC);

-- =====================================================
-- DECISION LIFECYCLE EVENTS
-- =====================================================

CREATE TABLE decision_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id VARCHAR(255) NOT NULL,
    event_type VARCHAR(100) NOT NULL CHECK (event_type IN ('intent_received', 'ready_received', 'task_generated', 'decision_published', 'error')),
    event_data JSONB DEFAULT '{}',
    processing_duration_ms DECIMAL(10,3),
    error_message TEXT,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for decision lifecycle tracking
CREATE INDEX idx_decision_events_decision_id ON decision_events(decision_id);
CREATE INDEX idx_decision_events_event_type ON decision_events(event_type);
CREATE INDEX idx_decision_events_timestamp ON decision_events(timestamp DESC);
CREATE INDEX idx_decision_events_decision_timestamp ON decision_events(decision_id, timestamp);
CREATE INDEX idx_decision_events_errors ON decision_events(timestamp DESC) WHERE error_message IS NOT NULL;

-- =====================================================
-- POLICY CONTRACTS AUDIT
-- =====================================================

CREATE TABLE policy_contracts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id VARCHAR(255) NOT NULL,
    policy_preset VARCHAR(50) NOT NULL CHECK (policy_preset IN ('public', 'dev', 'restricted')),
    template_id VARCHAR(100) NOT NULL,
    contract JSONB NOT NULL,
    constraints JSONB DEFAULT '{}',
    generated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for policy contract analytics
CREATE INDEX idx_policy_contracts_decision_id ON policy_contracts(decision_id);
CREATE INDEX idx_policy_contracts_policy_preset ON policy_contracts(policy_preset);
CREATE INDEX idx_policy_contracts_template_id ON policy_contracts(template_id);
CREATE INDEX idx_policy_contracts_generated_at ON policy_contracts(generated_at DESC);

-- =====================================================
-- MEMORY CONTEXT RETRIEVALS
-- =====================================================

CREATE TABLE memory_retrievals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id VARCHAR(255) NOT NULL,
    scenario VARCHAR(255) NOT NULL,
    retrieval_success BOOLEAN NOT NULL DEFAULT false,
    memories_retrieved INTEGER NOT NULL DEFAULT 0,
    retrieval_duration_ms DECIMAL(10,3),
    error_message TEXT,
    memory_context_preview TEXT,
    attempted_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for memory analytics
CREATE INDEX idx_memory_retrievals_decision_id ON memory_retrievals(decision_id);
CREATE INDEX idx_memory_retrievals_scenario ON memory_retrievals(scenario);
CREATE INDEX idx_memory_retrievals_success ON memory_retrievals(retrieval_success);
CREATE INDEX idx_memory_retrievals_attempted_at ON memory_retrievals(attempted_at DESC);

-- =====================================================
-- DEAD LETTER QUEUE EVENTS
-- =====================================================

CREATE TABLE dlq_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reason_code VARCHAR(100) NOT NULL,
    message TEXT NOT NULL,
    payload JSONB NOT NULL,
    source_topic VARCHAR(255),
    decision_id VARCHAR(255),
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for DLQ analytics
CREATE INDEX idx_dlq_events_reason_code ON dlq_events(reason_code);
CREATE INDEX idx_dlq_events_source_topic ON dlq_events(source_topic);
CREATE INDEX idx_dlq_events_decision_id ON dlq_events(decision_id);
CREATE INDEX idx_dlq_events_occurred_at ON dlq_events(occurred_at DESC);

-- =====================================================
-- FOREIGN KEY CONSTRAINTS
-- =====================================================

-- Link ready records to intent records
ALTER TABLE ready_records
ADD CONSTRAINT fk_ready_records_decision_id
FOREIGN KEY (decision_id) REFERENCES intent_records(decision_id)
ON DELETE CASCADE;

-- Link task specifications to intent records
ALTER TABLE task_specifications
ADD CONSTRAINT fk_task_specifications_decision_id
FOREIGN KEY (decision_id) REFERENCES intent_records(decision_id)
ON DELETE CASCADE;

-- Link decision events to intent records
ALTER TABLE decision_events
ADD CONSTRAINT fk_decision_events_decision_id
FOREIGN KEY (decision_id) REFERENCES intent_records(decision_id)
ON DELETE CASCADE;

-- Link policy contracts to intent records
ALTER TABLE policy_contracts
ADD CONSTRAINT fk_policy_contracts_decision_id
FOREIGN KEY (decision_id) REFERENCES intent_records(decision_id)
ON DELETE CASCADE;

-- Link memory retrievals to intent records
ALTER TABLE memory_retrievals
ADD CONSTRAINT fk_memory_retrievals_decision_id
FOREIGN KEY (decision_id) REFERENCES intent_records(decision_id)
ON DELETE CASCADE;

-- =====================================================
-- PERFORMANCE OPTIMIZATIONS
-- =====================================================

-- Composite indexes for common query patterns
CREATE INDEX idx_decision_events_complete_flow ON decision_events(decision_id, event_type, timestamp) WHERE event_type IN ('intent_received', 'ready_received', 'task_generated', 'decision_published');
CREATE INDEX idx_task_specs_context_template ON task_specifications(context_id, template_id, generated_at DESC);
CREATE INDEX idx_policy_contracts_preset_template ON policy_contracts(policy_preset, template_id, generated_at DESC);

-- =====================================================
-- DATA VALIDATION CONSTRAINTS
-- =====================================================

-- Ensure memory retrieval counts are non-negative
ALTER TABLE memory_retrievals ADD CONSTRAINT check_memories_retrieved_non_negative
    CHECK (memories_retrieved >= 0);

-- Ensure processing durations are positive
ALTER TABLE decision_events ADD CONSTRAINT check_processing_duration_positive
    CHECK (processing_duration_ms IS NULL OR processing_duration_ms >= 0);
ALTER TABLE memory_retrievals ADD CONSTRAINT check_retrieval_duration_positive
    CHECK (retrieval_duration_ms IS NULL OR retrieval_duration_ms >= 0);

-- Ensure non-empty required text fields
ALTER TABLE intent_records ADD CONSTRAINT check_user_prompt_not_empty
    CHECK (LENGTH(TRIM(user_prompt)) > 0);
ALTER TABLE ready_records ADD CONSTRAINT check_user_override_ref_not_empty
    CHECK (LENGTH(TRIM(user_override_ref)) > 0);
ALTER TABLE ready_records ADD CONSTRAINT check_override_hash_not_empty
    CHECK (LENGTH(TRIM(override_hash)) > 0);

-- =====================================================
-- DOCUMENTATION COMMENTS
-- =====================================================

COMMENT ON TABLE intent_records IS 'Initial decision intents received from Kafka';
COMMENT ON TABLE ready_records IS 'Ready events correlating with intents for task generation';
COMMENT ON TABLE task_specifications IS 'Generated task specifications with references';
COMMENT ON TABLE decision_events IS 'Lifecycle tracking for decision processing flow';
COMMENT ON TABLE policy_contracts IS 'Audit trail for policy contract generation';
COMMENT ON TABLE memory_retrievals IS 'Memory context retrieval attempts and results';
COMMENT ON TABLE dlq_events IS 'Dead letter queue events for error tracking';

COMMENT ON COLUMN intent_records.decision_id IS 'Unique identifier linking intent to ready and task';
COMMENT ON COLUMN intent_records.policy_preset IS 'Policy enforcement level: public, dev, restricted';
COMMENT ON COLUMN intent_records.template_id IS 'Task template identifier, auto for generic tasks';
COMMENT ON COLUMN ready_records.truth_pack_alias IS 'Truth pack reference for decision context';
COMMENT ON COLUMN ready_records.override_hash IS 'Hash of user truth override for integrity';
COMMENT ON COLUMN task_specifications.task_ref IS 'Storage reference for task specification artifact';
COMMENT ON COLUMN decision_events.event_type IS 'Lifecycle stage: intent_received → ready_received → task_generated → decision_published';
COMMENT ON COLUMN policy_contracts.contract IS 'Full policy contract JSON with constraints';
COMMENT ON COLUMN memory_retrievals.memories_retrieved IS 'Count of memories successfully retrieved';
COMMENT ON COLUMN dlq_events.reason_code IS 'Classification code for DLQ event (INTENT_INVALID, READY_INVALID, etc.)';