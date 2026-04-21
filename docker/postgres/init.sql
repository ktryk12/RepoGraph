-- RepoGraph Postgres init — køres automatisk ved første containeropstart.
-- Indeholder migration 001 + 002 + markerer dem som applied i _schema_migrations.

-- Migrations tracking
CREATE TABLE IF NOT EXISTS _schema_migrations (
    name       TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- 001: retrieval_traces
-- ============================================================

CREATE TABLE IF NOT EXISTS retrieval_traces (
    retrieval_id          TEXT        PRIMARY KEY,
    tenant_id             TEXT        NOT NULL,
    query                 TEXT,
    task_family           TEXT,
    token_budget          INTEGER,
    token_estimate        INTEGER,
    duration_ms           INTEGER,
    consumer              TEXT        DEFAULT 'generic',
    compressor_strategy   TEXT        DEFAULT 'none',
    pre_compress_tokens   INTEGER     DEFAULT 0,
    post_compress_tokens  INTEGER     DEFAULT 0,
    extra                 JSONB       DEFAULT '{}',
    persisted_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rt_tenant    ON retrieval_traces(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rt_family    ON retrieval_traces(task_family);
CREATE INDEX IF NOT EXISTS idx_rt_persisted ON retrieval_traces(persisted_at DESC);
CREATE INDEX IF NOT EXISTS idx_rt_strategy  ON retrieval_traces(compressor_strategy);

-- ============================================================
-- 002: task_memory, patches, test_failures, verifier_runs, usage_logs
-- ============================================================

CREATE TABLE IF NOT EXISTS task_memory (
    task_id         TEXT        PRIMARY KEY,
    tenant_id       TEXT        NOT NULL,
    query           TEXT,
    task_family     TEXT,
    working_set_id  TEXT,
    retrieval_id    TEXT,
    status          TEXT        DEFAULT 'open',
    flags           JSONB       DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tm_tenant ON task_memory(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tm_status ON task_memory(status);

CREATE TABLE IF NOT EXISTS task_patches (
    id              BIGSERIAL   PRIMARY KEY,
    task_id         TEXT        NOT NULL REFERENCES task_memory(task_id) ON DELETE CASCADE,
    tenant_id       TEXT        NOT NULL,
    attempt         INTEGER     DEFAULT 1,
    diff            TEXT,
    result          TEXT        DEFAULT 'pending',
    failure_reason  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tp_task ON task_patches(task_id);

CREATE TABLE IF NOT EXISTS task_patch_symbols (
    id          BIGSERIAL PRIMARY KEY,
    patch_id    BIGINT    NOT NULL REFERENCES task_patches(id) ON DELETE CASCADE,
    symbol      TEXT      NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tps_patch ON task_patch_symbols(patch_id);

CREATE TABLE IF NOT EXISTS task_test_failures (
    id              BIGSERIAL   PRIMARY KEY,
    task_id         TEXT        NOT NULL REFERENCES task_memory(task_id) ON DELETE CASCADE,
    tenant_id       TEXT        NOT NULL,
    test_file       TEXT,
    test_name       TEXT,
    error_summary   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ttf_task ON task_test_failures(task_id);

CREATE TABLE IF NOT EXISTS verifier_runs (
    id              BIGSERIAL   PRIMARY KEY,
    tenant_id       TEXT        NOT NULL,
    task_id         TEXT,
    repo_path       TEXT,
    steps           TEXT[]      DEFAULT '{}',
    passed          BOOLEAN,
    result_json     JSONB       DEFAULT '{}',
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vr_task   ON verifier_runs(task_id);
CREATE INDEX IF NOT EXISTS idx_vr_tenant ON verifier_runs(tenant_id);

CREATE TABLE IF NOT EXISTS usage_logs (
    id              BIGSERIAL   PRIMARY KEY,
    tenant_id       TEXT        NOT NULL,
    model_id        TEXT,
    capability      TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    latency_ms      INTEGER,
    task_id         TEXT,
    routed_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ul_tenant ON usage_logs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_ul_model  ON usage_logs(model_id);
CREATE INDEX IF NOT EXISTS idx_ul_routed ON usage_logs(routed_at DESC);

-- Mark migrations as applied så repograph-migrate ikke genkører dem
INSERT INTO _schema_migrations (name) VALUES
    ('001_retrieval_traces.sql'),
    ('002_task_memory.sql')
ON CONFLICT (name) DO NOTHING;
