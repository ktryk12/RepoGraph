-- Migration 003: model-aware token economy dimensions and outcome metrics.

ALTER TABLE retrieval_traces
    ADD COLUMN IF NOT EXISTS baseline_tokens INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS saved_tokens_vs_baseline INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cache_hit BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS cache_saved_tokens INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS reused_tokens INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS repo_revision TEXT,
    ADD COLUMN IF NOT EXISTS content_hash TEXT,
    ADD COLUMN IF NOT EXISTS session_id TEXT,
    ADD COLUMN IF NOT EXISTS task_hint TEXT,
    ADD COLUMN IF NOT EXISTS target_model TEXT,
    ADD COLUMN IF NOT EXISTS adapter_version TEXT DEFAULT 'v1',
    ADD COLUMN IF NOT EXISTS analysis_step_id TEXT,
    ADD COLUMN IF NOT EXISTS tokenizer_profile TEXT DEFAULT 'generic';

CREATE INDEX IF NOT EXISTS idx_rt_repo_revision ON retrieval_traces(tenant_id, repo_revision);
CREATE INDEX IF NOT EXISTS idx_rt_session ON retrieval_traces(tenant_id, session_id);
CREATE INDEX IF NOT EXISTS idx_rt_target_model ON retrieval_traces(tenant_id, target_model);

ALTER TABLE usage_logs
    ADD COLUMN IF NOT EXISTS repo_revision TEXT,
    ADD COLUMN IF NOT EXISTS content_hash TEXT,
    ADD COLUMN IF NOT EXISTS session_id TEXT,
    ADD COLUMN IF NOT EXISTS task_hint TEXT,
    ADD COLUMN IF NOT EXISTS target_model TEXT,
    ADD COLUMN IF NOT EXISTS adapter_version TEXT DEFAULT 'v1',
    ADD COLUMN IF NOT EXISTS analysis_step_id TEXT,
    ADD COLUMN IF NOT EXISTS tokenizer_profile TEXT DEFAULT 'generic',
    ADD COLUMN IF NOT EXISTS baseline_input_tokens INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS repograph_input_tokens INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS saved_tokens_vs_baseline INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cache_hit BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS cache_saved_tokens INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS reused_tokens INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS input_price_usd NUMERIC(14, 8) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS output_price_usd NUMERIC(14, 8) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS total_price_usd NUMERIC(14, 8) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS verified_success BOOLEAN;

CREATE INDEX IF NOT EXISTS idx_ul_repo_revision ON usage_logs(tenant_id, repo_revision);
CREATE INDEX IF NOT EXISTS idx_ul_session ON usage_logs(tenant_id, session_id);
CREATE INDEX IF NOT EXISTS idx_ul_task ON usage_logs(tenant_id, task_id);
CREATE INDEX IF NOT EXISTS idx_ul_verified ON usage_logs(tenant_id, verified_success);

CREATE OR REPLACE VIEW usage_token_economy AS
SELECT
    tenant_id,
    COALESCE(target_model, model_id) AS model_id,
    COUNT(*) AS calls,
    SUM(input_tokens + output_tokens) AS total_tokens,
    COUNT(DISTINCT task_id) FILTER (WHERE verified_success IS TRUE) AS verified_successes,
    SUM(baseline_input_tokens) AS baseline_input_tokens,
    SUM(saved_tokens_vs_baseline) AS saved_tokens_vs_baseline,
    SUM(cache_saved_tokens) AS cache_saved_tokens,
    SUM(reused_tokens) AS reused_tokens,
    SUM(total_price_usd) AS total_price_usd,
    SUM(input_tokens + output_tokens)::NUMERIC
        / NULLIF(COUNT(DISTINCT task_id) FILTER (WHERE verified_success IS TRUE), 0)
        AS tokens_per_verified_success,
    SUM(total_price_usd)
        / NULLIF(COUNT(DISTINCT task_id) FILTER (WHERE verified_success IS TRUE), 0)
        AS price_per_verified_success
FROM usage_logs
GROUP BY tenant_id, COALESCE(target_model, model_id);
