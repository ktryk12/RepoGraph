# RepoGraph

RepoGraph is a local code intelligence engine for source repositories.

It scans a codebase, parses source files into a persistent graph, and exposes that graph through:

- a REST API
- an MCP server for AI coding tools

The goal is simple: give coding agents architectural context before they edit code.

## What RepoGraph Can Do

RepoGraph can:

- walk a repository and detect supported source files
- respect `.gitignore` while indexing
- parse code with Tree-sitter
- store symbols and relationships in an embedded graph database
- answer questions like:
  - what symbols exist?
  - where is this symbol defined?
  - what does this function call?
  - who calls this function?
  - what is the blast radius if this symbol changes?

RepoGraph currently indexes these language families:

- Python
- TypeScript
- JavaScript
- Go
- Rust
- Java
- C
- C++
- C#
- Ruby

## What You Need To Use It

You need:

- Python 3.11 or newer
- a local source repository you want to index
- local package installation via `pip`

RepoGraph runs locally and stores its graph on disk. Your code does not need to leave your machine.

## Installation

### For normal local use

```bash
pip install .
```

### For development

```bash
pip install -e ".[dev]"
```

## Start The Program

RepoGraph has two main entrypoints.

### 1. REST API

Start the local API server:

```bash
python -m repograph.api.app
```

By default it listens on:

```text
http://127.0.0.1:8000
```

You can also use the installed console script:

```bash
repograph
```

### 2. MCP Server

Start the MCP stdio server:

```bash
python -m repograph.mcp_server.server
```

You can also use the installed console script:

```bash
repograph-mcp
```

This mode is intended for tools that speak MCP, such as agentic coding clients.

## Database Backend

RepoGraph uses the local `cogdb` backed store. This means all graphs are persisted as files directly on your machine.

If you don't provide any configuration, RepoGraph uses `.repograph` at the root.

```bash
REPOGRAPH_DB_BACKEND=cog
REPOGRAPH_DB_PATH=.repograph
```

### Multi-Tenant Magic (Dynamic Database)

RepoGraph natively supports serving multiple isolated environments (tenants) using the same running instance of the API. This gives you the ability to index multiple different projects completely separate from one another!

To do this, simply include an `X-Tenant-ID` header in your API requests. If this is present, RepoGraph dynamically generates a new database folder (`.repograph_TENANTID`) automatically! No databases to provision.

Example API Call:
```bash
curl "http://127.0.0.1:8000/status" -H "X-Tenant-ID: ServiceA"
```

If you are using the MCP Server and want your specific AI coding tool to connect to a specific tenant directly, you just export an environment variable before starting:

```bash
set REPOGRAPH_TENANT_ID=ServiceA
repograph-mcp
```



## Where Data Is Stored

By default RepoGraph stores graph data in:

```text
.repograph
```

You can override that location with:

```bash
REPOGRAPH_DB_PATH=/path/to/graphdb
```

For the API server you can also override host and port:

```bash
REPOGRAPH_HOST=127.0.0.1
REPOGRAPH_PORT=8000
```

## Typical Workflow

1. Start the API server.
2. Send an index request for a repository.
3. Query symbols, relationships, and blast radius.
4. Optionally start the MCP server and connect it to an AI coding tool.

## REST API Endpoints

### Health check

```http
GET /health
```

Returns basic service status and version.

### Index a repository

```http
POST /index
```

Request body:

```json
{
  "repo_path": "/abs/path/to/repo",
  "force": false
}
```

Behavior:

- `repo_path` must point to a local repository folder
- `force=true` clears the current graph before reindexing

Response example:

```json
{
  "status": "ok",
  "files_indexed": 1105,
  "triples_added": 82936,
  "duration_ms": 35758
}
```

### Check indexing status

```http
GET /status
```

Response example:

```json
{
  "indexed": true,
  "repo_path": "E:/repos/example-repo",
  "node_count": 25372,
  "last_indexed": "2026-03-23T20:11:27Z"
}
```

### Search symbols

```http
GET /symbols?q=RepoGraph&limit=20
```

Response:

```json
{
  "symbols": [
    "repograph.graph.store.RepoGraph",
    "repograph.graph.store.RepoGraph.put_triple"
  ]
}
```

### Get one symbol

```http
GET /symbol/repograph.graph.store.RepoGraph.put_triple
```

Response shape:

```json
{
  "symbol": "repograph.graph.store.RepoGraph.put_triple",
  "in_file": "repograph/graph/store.py",
  "at_line": "25",
  "calls": [],
  "called_by": [],
  "defines": [],
  "defined_by": "repograph.graph.store.RepoGraph"
}
```

### Get blast radius

```http
GET /blast-radius/repograph.graph.store.RepoGraph.put_triple?depth=3
```

Response shape:

```json
{
  "symbol": "repograph.graph.store.RepoGraph.put_triple",
  "depth": 3,
  "affected": {
    "repograph.graph.store.RepoGraph.put_triple": []
  }
}
```

### Get all symbols in a file

```http
GET /file/repograph/graph/store.py
```

Response:

```json
{
  "filepath": "repograph/graph/store.py",
  "symbols": [
    "repograph.graph.store.RepoGraph",
    "repograph.graph.store.RepoGraph.put_triple"
  ]
}
```

## Example: Index A Repository From The Command Line

With the API running:

```bash
curl -X POST http://127.0.0.1:8000/index ^
  -H "Content-Type: application/json" ^
  -d "{\"repo_path\":\"E:/repos/some-repo\",\"force\":true}"
```

Then query it:

```bash
curl "http://127.0.0.1:8000/symbols?q=RepoGraph&limit=10"
```

## MCP Tools Exposed

The MCP server exposes these tools:

- `index_repo(repo_path, force=False)`
- `search_symbols(query, limit=20)`
- `get_symbol(symbol)`
- `blast_radius(symbol, depth=3)`
- `repo_status()`

These tools reuse the same underlying graph and indexing logic as the REST API.

## Current Scope

RepoGraph is intentionally focused.

Current non-goals in this phase:

- no Docker or container requirement
- no cloud dependency
- no web UI
- no multi-repo graph (without explicitly enabling X-Tenant-ID headers)
- no watch mode / auto reindex on file changes
- no semantic vector search

Cross-file name resolution is currently best-effort, not full semantic resolution.

## Development

Run tests:

```bash
python -m pytest tests -q
```

## Summary

Use RepoGraph when you want a local, persistent code graph that helps humans and AI tools understand:

- what exists in a codebase
- how symbols relate to each other
- where changes may have downstream impact

Start the API if you want HTTP access.
Start the MCP server if you want direct integration with an MCP-capable coding tool.
