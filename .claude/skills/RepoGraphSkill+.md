You are refactoring and extending an existing local code intelligence platform called RepoGraph.

## Context

RepoGraph is a **local code intelligence platform for AI coding agents**.

It analyzes a repository with Tree-sitter, builds a persistent knowledge graph, and exposes that graph via a REST API and MCP server. Consumers such as Claude Code, Codex, ekstern agentplatform and NewModel fetch structural context from RepoGraph — RepoGraph itself **never calls any LLM**.

Current architecture:

- Consumer (Claude Code / Codex / ekstern agentplatform / NewModel)
  │
  ▼
- RepoGraph MCP / REST API
  │
  ├─ Shared Retrieval Gateway
  │    ├─ Redis (hot cache — summaries, working sets, prompt packs)
  │    ├─ Context Compressor (LongCodeZip-inspired, no LLM)
  │    └─ Prompt Packer (5 strategies)
  │
  ├─ Graph Store / CogDB (structural source of truth)
  │    ├─ Symbols, files, relations (CALLS, IMPORTS, DEFINES, TESTS, …)
  │    ├─ Enrichment (risk level, signature, service, test/entrypoint flags)
  │    ├─ Summaries (L0 repo → L1 service → L2 file → L3 symbol)
  │    └─ Knowledge graph (docs, CODEOWNERS, CI workflows, ADRs)
  │
  └─ Postgres (operational store)
       ├─ retrieval_traces (token estimates, compression metrics)
       ├─ task_memory (patch attempts, test failures, precision signals)
       ├─ verifier_runs (lint, typecheck, pytest results)
       └─ usage_logs (model, tokens, latency)

Supported languages: Python · TypeScript · JavaScript · Go · Rust · Java · C · C++ · C# · Ruby

RepoGraph is **pure structural retrieval**. It does not call LLMs; consumers generate summaries and write them back via PUT endpoints.

## Goal

Extend RepoGraph with a **unified token-economy and context-engineering layer** so that:

- Token usage is minimized for all workflows (coding agents, film/video workflows, trading bots checked by LLMs).
- Every model call sees only the **minimal necessary information**, exactly once.
- Token cost and latency are measured and controlled per task, per model, per consumer.
- RepoGraph becomes a “ContextOS” that is **model-independent** and owns:
  - capability discovery,
  - context budgeting,
  - progressive context delivery,
  - cache and token accounting.

We want to evolve RepoGraph so that it can:

1. Maintain a **Capability Graph**:
   - MCP tools
   - REST/OpenAPI functions
   - CLI commands
   - scripts
   - skills and workflows
   - test/lint/build tools
   - databases and document sources
   - local and external models

   Each capability has a manifest, for example:

   ```json
   {
     "id": "pytest.targeted",
     "description": "Run relevant Python tests",
     "input_schema": {},
     "output_schema": {},
     "risk": "read_only",
     "estimated_tokens": 120,
     "estimated_latency_ms": 3000,
     "requires": ["python", "pytest"],
     "produces": ["verification_result"],
     "permissions": ["execute_tests"]
   }
   ```

   RepoGraph should only return the N most relevant capabilities/tools for the next step, and expose a `discover_capabilities` mechanism if the consumer needs more.

2. Implement a **single, correct token-budget engine**:

   Today RepoGraph mostly estimates tokens via characters/4. This is not precise enough for universal routing.

   The budget engine must compute:

   - total context window
     - system instructions
     - required tool schemas
     - active task memory
     - code and documentation
     - tool results
     - reserved output
     - safety margin
   - available retrieval budget

   It must support tokenizer profiles per model family (OpenAI, Anthropic, Gemini, local models…) plus a generic fallback. The **same engine** must be used by retrieval, compression and prompt packing so context is not budgeted three different ways.

3. Deliver **progressive context instead of giant prompts**:

   RepoGraph should deliver context in levels:

   - L0: repository/project overview
   - L1: relevant services/modules
   - L2: relevant files
   - L3: symbols and signatures
   - L4: precise code spans
   - L5: full file only when truly needed

   Consumers start cheap (L0–L3) and request L4/L5 on demand. After each action only the **delta** since last step is sent, not the full WorkingSet again.

4. Own a **controlled agent-loop state** (LLM-free):

   RepoGraph owns the loop state; the model only chooses or suggests next action:

   - CLASSIFY
   - DISCOVER relevant capabilities
   - RETRIEVE minimal WorkingSet
   - ACT
   - VERIFY without LLM
   - store DELTA and result
   - STOP or RETRY with small failure-pack

   The loop enforces hard limits:

   - maximum number of model calls
   - token and price budget
   - time limit
   - stop on repeated identical actions
   - read-only by default
   - explicit approval for writes/external actions
   - automatic cheap model for simple subtasks
   - verifier results instead of asking the model to guess

   RepoGraph already has retry-pack and verifier layers which can be extended.

5. Support **universal model integration**, without leaking RepoGraph internals:

   All models are supported at the application level, but not all can perform stable tool calls.

   RepoGraph must support four integration levels:

   - MCP-native: Codex, Claude and other MCP hosts, with capability negotiation and standardized tools/resources/prompts.
   - Native function calling: OpenAI, Anthropic, Gemini, Mistral formats. OpenAI supports function tools and remote MCP; Gemini uses structured function declarations.
   - OpenAI-compatible local models: e.g. Qwen, vLLM, Ollama via adapter layer.
   - Models without tool calling: RepoGraph host orchestrator uses a simple, validated JSON/ReAct format and executes tools itself, validating and repairing tool calls when needed.

   The model does **not** need to know RepoGraph’s internal implementation.

## Current persistence and gaps

RepoGraph currently stores data in three layers:

- CogDB graph (local):
  - files, functions, classes, symbol names
  - file locations and line numbers
  - signatures
  - relations: CALLS, IMPORTS, INHERITS, TESTS, …
  - service, risk level, test/entrypoint
  - docs, CI, ownership, ADR-relations
  - L0–L3 summaries (when consumers write back)
  - retrieval query, selected symbols, number of files, token estimate, duration

- Postgres (if enabled):
  - user task/query
  - task family and status
  - WorkingSet and retrieval IDs
  - patch/diff and affected symbols
  - failure causes
  - test names and failure description
  - verifier results
  - model used
  - input/output tokens
  - latency
  - compression method
  - tokens before and after compression

- Redis (temporary, TTL):
  - repo/service/file/symbol summaries (≈1 hour)
  - complete WorkingSet responses (≈10 minutes)
  - session snapshots (≈5 minutes)
  - latest verifier result (≈5 minutes)

RepoGraph currently does **not** systematically store:

- full chat history
- all prompts and model responses
- precise token savings vs baseline
- price per model call
- tool calls and tool results as single workflows
- capability/tool registry
- source hash per summary
- complete session deltas
- precise tokenizer info per model

We want to add those **only in compact, structured forms**, not as raw prompt blobs.

## What you must do

You will extend and refactor the RepoGraph codebase. DO NOT design a new system from scratch.

### 1. Design a Token Budget Engine

- Introduce a central token budget module (e.g. `repograph/token_budget/engine.py`) that:
  - Exposes an API to compute token budgets for:
    - retrieval
    - compression
    - prompt packing
  - Uses tokenizer profiles per model family (OpenAI, Anthropic, Gemini, local).
  - Replaces the current naive `chars/4` estimation, but keeps a fallback.

- Integrate this engine into:
  - Shared Retrieval Gateway
  - Context Compressor
  - Prompt Packer
  - Postgres usage logging (so we can compute metrics like `tokens_per_verified_success`, `saved_tokens_vs_baseline`, etc.)

### 2. Implement a Capability Graph and Registry

- Define a `CapabilityManifest` data model (Python + JSON schema) that matches the manifest example above.
- Add a capability registry layer (e.g. `repograph/capabilities/registry.py`) that:
  - Can load/declare capabilities for:
    - MCP tools
    - REST/OpenAPI endpoints
    - CLI commands
    - scripts and workflows
    - verifier tools (pytest, ruff, mypy, bandit)
  - Stores manifests in CogDB and/or Postgres with proper IDs and versioning.
  - Supports ranking and `discover_capabilities` based on:
    - current task/query
    - risk level
    - estimated tokens and latency
    - required/produced artifacts
    - historical success signals (from task_memory/verifier_runs).

- Add REST and MCP interfaces:
  - REST: `/capabilities/search`, `/capabilities/{id}`, `/capabilities/discover`.
  - MCP tools: `discover_capabilities`, `get_capability_manifest`, `rank_capabilities_for_task`.

### 3. Extend the WorkingSet and context models for progressive context + deltas

- Extend WorkingSet model to include:
  - first-class tests, configs, code spans
  - provenance (source summary hashes, graph revision)
  - constraints (read-only, token budget, write permissions)
  - delta references (what changed since last step)

- Add APIs for:
  - Progressive context levels L0–L5.
  - “delta packs” that only contain changes since the last WorkingSet/prompt pack.

- Ensure Redis + Postgres can:
  - store and retrieve session snapshots and deltas
  - reconstruct WorkingSets from persistent data when needed.

### 4. Implement a controlled agent-loop state (without LLM)

- Introduce an internal loop-state module (e.g. `repograph/agent_loop/state.py`) that:
  - Represents the sequence CLASSIFY → DISCOVER → RETRIEVE → ACT → VERIFY → DELTA → STOP/RETRY.
  - Enforces hard limits:
    - max number of model calls
    - token and price budget per task/session
    - time limit
    - stop-on-repeated-identical-actions
    - read-only by default
    - explicit approval before writes/external actions
    - automatic routing to cheaper models for simple subtasks.

- Integrate verifier results so that:
  - Tool calls are validated and schema-checked.
  - Retry packs are generated from structured failure data, not from free-form text.

### 5. Strengthen usage logs and metrics

- Extend `usage_logs` to track:
  - baseline vs RepoGraph token usage
  - cache savings (cache-hit vs fresh retrieval)
  - reused tokens
  - price per call and per verified success
  - `tokens_per_verified_success` as a primary metric.

- Make sure session and model identifiers (session_id, target_model, adapter_version) are used in cache keys and logs:
  - Include repository revision/content hash, task hint, target model, adapter version, analysis step.

### 6. Universal model support

- Add adapter registry for consumer adapters (instead of hard-coded types).
- Support integration levels:
  - MCP-native (Codex, Claude, etc.)
  - Native function calling (OpenAI, Anthropic, Gemini, Mistral)
  - OpenAI-compatible local models (Qwen, vLLM, Ollama)
  - Models without tool calling via JSON/ReAct format.

- Make sure RepoGraph does not leak internal implementation details to models:
  - It should expose capabilities, context packs and verification plans.
  - Consumers own final prompt/message assembly.

## Constraints

- Do NOT add direct LLM calls inside RepoGraph.
- Keep RepoGraph as a **structural context and token-economy layer**.
- Prefer small, focused modules and clear interfaces over monolithic changes.
- Write tests for:
  - token budget engine
  - capability registry and ranking
  - progressive context (L0–L5)
  - agent-loop state limits
  - usage logs and token metrics.

## Deliverables

Start by:

1. Designing the token budget engine API and data structures.
2. Implementing the CapabilityManifest and registry, with at least one example capability per existing MCP tool/verifier.
3. Extending WorkingSet to support deltas and progressive context levels.
4. Wiring usage logs to compute `tokens_per_verified_success`.

Propose concrete file changes, new modules and migrations for Postgres/CogDB/Redis. Then implement them incrementally, keeping existing behaviour working for current consumers.