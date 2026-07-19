<#
.SYNOPSIS
    Wire RepoGraph auto-indexing into EVERY future `git clone` / `git init` (Windows).

.DESCRIPTION
    Installs the auto-index hooks into a git template directory and points
    `git config --global init.templateDir` at it. From then on, every repo you
    clone or init copies these hooks automatically — RepoGraph indexes on first
    checkout and re-indexes on pull / commit / rebase, with no per-repo setup.
    Existing repos are unaffected; use Install-GitHooks.ps1 for those.

.EXAMPLE
    scripts\autoindex\Setup-GlobalAutoindex.ps1
#>
param(
    [string]$TemplateDir = (Join-Path $HOME ".git-templates")
)

$ErrorActionPreference = "Stop"

$hooksDir = Join-Path $TemplateDir "hooks"
New-Item -ItemType Directory -Force -Path $hooksDir | Out-Null

$hookBody = @'
#!/usr/bin/env bash
# RepoGraph auto-index hook (installed globally via git template)
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
if command -v repograph-autoindex >/dev/null 2>&1; then
  repograph-autoindex "$REPO_ROOT" --quiet || true
else
  python -m repograph.autoindex "$REPO_ROOT" --quiet || true
fi
'@

foreach ($hook in @("post-checkout", "post-merge", "post-commit", "post-rewrite")) {
    $path = Join-Path $hooksDir $hook
    Set-Content -Path $path -Value $hookBody -Encoding utf8 -NoNewline
    Write-Host "installed $hook -> $path"
}

git config --global init.templateDir $TemplateDir

Write-Host ""
Write-Host "done. init.templateDir = $(git config --global init.templateDir)"
Write-Host "Every future 'git clone' / 'git init' now auto-indexes with RepoGraph."
Write-Host "For repos you already have, run: scripts\autoindex\Install-GitHooks.ps1 -Repo <repo>"
