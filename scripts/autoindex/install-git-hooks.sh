#!/usr/bin/env bash
# Install RepoGraph auto-index git hooks into a target repository.
#
# After install, RepoGraph re-indexes the repo automatically whenever HEAD
# changes — i.e. on clone-then-checkout, branch switch, merge, pull, commit,
# and rebase. The hook is a cheap no-op when the graph is already current.
#
# Usage:
#   scripts/autoindex/install-git-hooks.sh [/path/to/repo]   # default: cwd
set -euo pipefail

REPO="${1:-$(pwd)}"
HOOKS_DIR="$(git -C "$REPO" rev-parse --git-path hooks)"

if [ -z "$HOOKS_DIR" ]; then
  echo "error: $REPO is not a git repository" >&2
  exit 1
fi

mkdir -p "$HOOKS_DIR"

install_hook() {
  local name="$1"
  local path="$HOOKS_DIR/$name"
  cat > "$path" <<'HOOK'
#!/usr/bin/env bash
# RepoGraph auto-index hook (managed by install-git-hooks.sh)
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
if command -v repograph-autoindex >/dev/null 2>&1; then
  repograph-autoindex "$REPO_ROOT" --quiet || true
else
  python -m repograph.autoindex "$REPO_ROOT" --quiet || true
fi
HOOK
  chmod +x "$path"
  echo "installed $name -> $path"
}

for hook in post-checkout post-merge post-commit post-rewrite; do
  install_hook "$hook"
done

echo "done. RepoGraph will auto-index $REPO on HEAD changes."
