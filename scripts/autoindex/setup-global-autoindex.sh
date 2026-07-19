#!/usr/bin/env bash
# Wire RepoGraph auto-indexing into EVERY future `git clone` / `git init`.
#
# It installs the auto-index hooks into a git template directory and points
# `git config --global init.templateDir` at it. From then on, every repo you
# clone or init copies these hooks automatically — so RepoGraph indexes the repo
# on first checkout, and re-indexes on pull / commit / rebase — with no per-repo
# setup. Existing repos are unaffected; run install-git-hooks.sh for those.
#
# Usage:
#   scripts/autoindex/setup-global-autoindex.sh
#   TEMPLATE_DIR=~/.config/git/template scripts/autoindex/setup-global-autoindex.sh
set -euo pipefail

TEMPLATE_DIR="${TEMPLATE_DIR:-$HOME/.git-templates}"
HOOKS_DIR="$TEMPLATE_DIR/hooks"

mkdir -p "$HOOKS_DIR"

for hook in post-checkout post-merge post-commit post-rewrite; do
  path="$HOOKS_DIR/$hook"
  cat > "$path" <<'HOOK'
#!/usr/bin/env bash
# RepoGraph auto-index hook (installed globally via git template)
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
if command -v repograph-autoindex >/dev/null 2>&1; then
  repograph-autoindex "$REPO_ROOT" --quiet || true
else
  python -m repograph.autoindex "$REPO_ROOT" --quiet || true
fi
HOOK
  chmod +x "$path"
  echo "installed $hook -> $path"
done

git config --global init.templateDir "$TEMPLATE_DIR"

echo ""
echo "done. git config --global init.templateDir = $(git config --global init.templateDir)"
echo "Every future 'git clone' / 'git init' now auto-indexes with RepoGraph."
echo "For repos you already have, run: scripts/autoindex/install-git-hooks.sh <repo>"
