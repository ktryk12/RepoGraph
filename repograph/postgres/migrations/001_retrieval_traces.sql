-- Migration 001: retrieval_traces + komprimeringskolonner
-- Køres én gang mod Postgres-databasen.

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

CREATE INDEX IF NOT EXISTS idx_rt_tenant      ON retrieval_traces(tenant_id);
CREATE INDEX IF NOT EXISTS idx_rt_family      ON retrieval_traces(task_family);
CREATE INDEX IF NOT EXISTS idx_rt_persisted   ON retrieval_traces(persisted_at DESC);
CREATE INDEX IF NOT EXISTS idx_rt_strategy    ON retrieval_traces(compressor_strategy);
