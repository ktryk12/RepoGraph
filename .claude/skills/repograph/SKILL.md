---
name: repograph
description: Use RepoGraph (local code-intelligence graph via MCP) to gather precise, token-budgeted context for ANY coding task in an indexed repo — instead of blindly reading many files. Use when starting a bug fix, refactor, feature, or "understand this repo" task, before editing a symbol (blast radius), and after editing (verify + write back memory). Triggers: "fix", "refactor", "where is", "what calls", "understand the repo", "add feature".
---

# Using RepoGraph for coding tasks

RepoGraph is a local, LLM-free code graph exposed over MCP. It answers structural
questions and hands back **compressed, token-budgeted context** so you don't burn
tokens reading whole files. Prefer its tools over ad-hoc `grep`/file-reads for
understanding an indexed repo.

## 0. Precondition — is the repo indexed?
Call `repo_status`. If it reports not indexed (or a different repo), run
`index_repo(repo_path)` once. With auto-indexing wired (session/git hooks) this is
usually already done — but always check, and re-index with `index_repo(force=True)`
if you need the graph to reflect **uncommitted** working-tree changes mid-task.

## 1. Start every task with `prepare_task_context`
This is the primary entry point — one call runs classify → retrieve → compress → pack.

```
prepare_task_context(
  repo_path,
  query="<the task in one sentence>",
  output_profile="small",   # tiny|small|medium|patch|review
  consumer="claude_code",
)
```
Use the returned `prompt_pack` / `working_set` as your grounding. Pick the profile
by task: `patch` for a targeted fix, `review` for code review, `medium` for deep
analysis, `small` (default) otherwise. Keep the `retrieval_trace_id` and `task_id`.

## 2. Broad "understand the whole repo" asks → `build_analysis_plan`
Don't force one giant prompt. `build_analysis_plan(repo_path, query)` returns an
ordered set of steps (repo overview → services → high-risk files → entrypoints →
tests → …). Work them one at a time.

## 3. Navigate the graph for targeted questions
- `search_symbols(query)` → candidate symbol IDs.
- `get_symbol(symbol)` → file, line, callers, callees, ownership.
- `find_relevant_symbols(query)` → coarse candidates for a task.

## 4. BEFORE editing a symbol → `blast_radius`
```
blast_radius(symbol, depth=3)
```
See everything that transitively calls it, so you know what your change can break
and what to test. Do this before any non-trivial edit.

## 5. AFTER editing → verify, then close the loop
- `verify_task_context(repo_path, files=[...], task_id=...)` — runs lint / type
  check / tests on the changed files. Read the result before claiming success.
- If it fails: `build_retry_pack(repo_path, query, failure_reason, previous_diff)`
  gives fresh patch-first context with the failure up front. Iterate.
- `update_task_memory(task_id, patch={...})` — record the attempt / outcome so the
  next retrieval is sharper.
- Optionally PUT summaries (file/symbol/service) so the graph accumulates knowledge
  (RepoGraph is LLM-free and never writes these itself).

## Typical loop (bug fix)
1. `prepare_task_context(repo_path, "fix token refresh bug", output_profile="patch")`
2. `blast_radius("refresh_token")` → know the impact surface
3. make the edit
4. `verify_task_context(repo_path, ["auth.py"], task_id=...)`
5. green → `update_task_memory(...)`; red → `build_retry_pack(...)` and retry

## Notes
- Read-only by default (24 tools). Filesystem write tools exist but are gated
  behind `REPOGRAPH_ENABLE_WRITE_TOOLS=1` — don't rely on them.
- Multi-tenant: each repo/project can use its own tenant (isolated graph).
- If MCP tools aren't available, the same operations exist as REST endpoints on the
  API (`POST /shared-retrieval/prepare`, `GET /blast-radius/{symbol}`, `POST /verify`).
