-- Migration 002: task_memory, patches, test_failures, verifier_runs, usage_logs

CREATE TABLE IF NOT EXISTS task_memory (
    task_id         TEXT        PRIMARY KEY,
    tenant_id       TEXT        NOT NULL,
    query           TEXT,
    task_family     TEXT,
    working_set_id  TEXT,
    retrieval_id    TEXT,
    status          TEXT        DEFAULT 'open',   -- open | patched | verified | failed
    flags           JSONB       DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tm_tenant ON task_memory(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tm_status ON task_memory(status);

-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS task_patches (
    id              BIGSERIAL   PRIMARY KEY,
    task_id         TEXT        NOT NULL REFERENCES task_memory(task_id) ON DELETE CASCADE,
    tenant_id       TEXT        NOT NULL,
    attempt         INTEGER     DEFAULT 1,
    diff            TEXT,
    result          TEXT        DEFAULT 'pending',  -- pending | applied | failed
    failure_reason  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tp_task ON task_patches(task_id);

-- -----------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS task_patch_symbols (
    id          BIGSERIAL PRIMARY KEY,
    patch_id    BIGINT    NOT NULL REFERENCES task_patches(id) ON DELETE CASCADE,
    symbol      TEXT      NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tps_patch ON task_patch_symbols(patch_id);

-- -----------------------------------------------------------------------

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

-- -----------------------------------------------------------------------

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

-- -----------------------------------------------------------------------

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
