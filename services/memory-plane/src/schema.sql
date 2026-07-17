PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    domains TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_active TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS episodes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    status TEXT NOT NULL,
    turns TEXT NOT NULL,
    result TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memory_entries (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('event', 'working', 'knowledge')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_projects_last_active ON projects(last_active);
CREATE INDEX IF NOT EXISTS idx_episodes_project_domain ON episodes(project_id, domain);
CREATE INDEX IF NOT EXISTS idx_episodes_project_status ON episodes(project_id, status);
CREATE INDEX IF NOT EXISTS idx_memory_entries_project_domain ON memory_entries(project_id, domain);
CREATE INDEX IF NOT EXISTS idx_memory_entries_project_domain_type ON memory_entries(project_id, domain, type);
