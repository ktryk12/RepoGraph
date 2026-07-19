---
name: repograph
description: Use RepoGraph's local, LLM-free MCP/REST code graph to retrieve minimal token-budgeted context for coding tasks and to guide changes to RepoGraph or ContextOS internals. Trigger for repository understanding, symbol lookup, bug fixes, features, refactors, blast-radius analysis, WorkingSet/prompt/retry packing, token economy, capability graph, cache/metrics, verification, and RepoGraph architecture work. Prefer RepoGraph over broad file reads when its MCP tools are available.
---

# RepoGraph

Use RepoGraph as the structural context layer for coding work. Keep context small,
grounded, and verifiable.

## Route the task

- For work **in any indexed repository**, follow the MCP workflow below.
- For changes to **RepoGraph/ContextOS internals**, first read
  [`../RepoGraphSkill+.md`](../RepoGraphSkill+.md) completely and treat it as the
  architecture/design specification.
- Do not load the full design specification for unrelated projects.

## MCP workflow for coding tasks

1. Call `repo_status`. If the target repository is missing or stale, call
   `index_repo(repo_path)`; use `force=True` when uncommitted changes must be
   reflected.
2. Call `prepare_task_context` with the target repository, a one-sentence query,
   an appropriate profile (`patch`, `review`, `medium`, or `small`), the target
   model, and its context limit when known.
3. Use the returned `prompt_pack`, `working_set`, `task_id`, and
   `retrieval_trace_id` as the initial grounding. Do not reread broad portions of
   the repository unless the pack shows a concrete gap.
4. For broad analysis, call `build_analysis_plan` and execute one step at a time.
5. Before a non-trivial symbol edit, call `search_symbols`/`get_symbol`, then
   `blast_radius(symbol, depth=3)`.
6. After editing, call `verify_task_context` with the changed files and task ID.
   On failure, call `build_retry_pack` with the verifier failure and prior diff.
7. Record the outcome with `update_task_memory`; refresh the index after accepted
   structural changes.

Use REST equivalents only when MCP is unavailable. If neither interface is
available, continue with normal repository tools and state that RepoGraph context
was unavailable.

## RepoGraph/ContextOS implementation rules

After reading the full design specification:

1. Keep RepoGraph internally LLM-free and model-independent.
2. Preserve existing MCP/REST response contracts unless the user explicitly
   authorizes a breaking change.
3. Reuse the central token-budget engine across retrieval, compression, prompt
   packing, retries, and usage accounting; never add new `chars/4` estimates.
4. Key caches and metrics by repository revision/content hash, session/task,
   target model, tokenizer profile, and adapter version as applicable.
5. Prefer focused changes over rewrites and keep CogDB, Redis, and Postgres roles
   distinct.
6. Add or update tests for token budgets, cache identity, prompt packing,
   verification, and usage metrics.
7. Verify the smallest relevant test set first, then the complete RepoGraph suite.

## Context discipline

- Start with summaries, symbols, signatures, and relations; request code spans or
  full files only when required.
- Send deltas between steps instead of repeating the full WorkingSet.
- Return only relevant tool/capability schemas for the next action.
- When generating a prompt for another model, summarize only the relevant design
  constraints; do not copy the complete design specification by default.
