# Auto-indexing — activate RepoGraph on every new/updated repo

RepoGraph does **not** re-index on file changes by itself. Auto-indexing wires the
index into the events that mean *"a repo appeared"* or *"the repo moved to a new
state"* — a clone, a checkout, a merge/pull, a commit, or an AI agent starting a
session — so the graph is always fresh without anyone remembering to run `/index`.

The engine is `repograph.autoindex`, exposed as the `repograph-autoindex` console
script. It computes a cheap **signature** (git `HEAD`, or newest source-file mtime
for non-git repos), compares it to the last indexed state stored next to the graph
(`.repograph*/autoindex_state.json`), and **re-indexes only when the signature
changed**. Running it when nothing changed is a fast no-op, so it is safe to call
on every event.

```bash
repograph-autoindex                 # index cwd if it changed
repograph-autoindex /path/to/repo   # index a specific repo
repograph-autoindex --force         # re-index unconditionally
repograph-autoindex --check         # exit 1 if stale, don't index (for CI gates)
repograph-autoindex --api-url http://localhost:8001   # index via a running API
```

Pick the layer(s) that match how repos enter your workflow.

---

## 1. Git hooks — activate on clone / checkout / pull / commit

Installs `post-checkout`, `post-merge`, `post-commit`, and `post-rewrite` hooks.
Together they cover: first checkout after `git clone`, branch switches, `git pull`,
new commits, and rebases.

```bash
# macOS / Linux / Git Bash
scripts/autoindex/install-git-hooks.sh /path/to/repo

# Windows PowerShell
scripts\autoindex\Install-GitHooks.ps1 -Repo E:\repos\my-project
```

To auto-install these hooks into **every** repo you clone or init — so RepoGraph
is created and kept up to date the moment any new repo lands — run the global
setup once:

```bash
scripts/autoindex/setup-global-autoindex.sh              # macOS / Linux / Git Bash
scripts\autoindex\Setup-GlobalAutoindex.ps1              # Windows PowerShell
```

This installs the hooks into a git template dir and sets
`git config --global init.templateDir`. Every future `git clone` / `git init`
then inherits the auto-index hooks automatically. Existing repos are unaffected —
use `install-git-hooks.sh` / `Install-GitHooks.ps1` for those.

---

## 2. Claude Code — activate when an AI session starts

Add a `SessionStart` hook so RepoGraph indexes the working repo the moment Claude
Code opens it. Put this in the project's `.claude/settings.json` (or your user
settings):

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "repograph-autoindex \"$CLAUDE_PROJECT_DIR\" --quiet"
          }
        ]
      }
    ]
  }
}
```

Now every session begins with a current graph, and the agent can call
`prepare_task_context` immediately. Because the check is signature-gated, sessions
where nothing changed pay almost nothing.

> Prefer indexing through the running API? Use
> `repograph-autoindex "$CLAUDE_PROJECT_DIR" --api-url http://localhost:8001 --quiet`.

---

## 3. MCP / programmatic — activate from your own agent loop

Call it directly before the first retrieval in a task:

```python
from repograph.autoindex import ensure_indexed

result = ensure_indexed("/path/to/repo", tenant="projectA")
# result["action"] in {"indexed", "skipped"}
```

`ensure_indexed` is idempotent, so agents can call it at the top of every task
without worrying about redundant work.

---

## 4. CI / container startup — activate on build or deploy

* **CI:** add a step `repograph-autoindex --api-url "$REPOGRAPH_URL"` after checkout,
  or gate merges with `repograph-autoindex --check`.
* **Container:** run `repograph-autoindex /workspace` as an init step after the repo
  is mounted/cloned, before serving retrieval traffic.

---

## How staleness is decided

| Repo type | Signature | Re-indexes when |
|---|---|---|
| git | current `HEAD` commit | HEAD changes (commit, checkout, merge, pull, rebase) |
| non-git | newest source mtime + file count | any tracked source file is added/modified |

State lives at `<REPOGRAPH_DB_PATH>[_<tenant>]/autoindex_state.json`. Delete it (or
pass `--force`) to force a full re-index. Working-tree edits that are **not** yet
committed do not change the git signature — index those explicitly with
`repograph-autoindex --force` or the `index_repo` MCP tool when you need the graph
to reflect uncommitted work mid-task.
