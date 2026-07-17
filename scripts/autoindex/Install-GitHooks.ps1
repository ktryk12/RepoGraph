<#
.SYNOPSIS
    Install RepoGraph auto-index git hooks into a target repository (Windows).

.DESCRIPTION
    After install, RepoGraph re-indexes the repo automatically whenever HEAD
    changes (checkout, merge, pull, commit, rebase). The hook is a cheap no-op
    when the graph is already current.

.EXAMPLE
    scripts\autoindex\Install-GitHooks.ps1 -Repo E:\repos\my-project
#>
param(
    [string]$Repo = (Get-Location).Path
)

$ErrorActionPreference = "Stop"

$hooksDir = git -C $Repo rev-parse --git-path hooks
if (-not $hooksDir) {
    Write-Error "$Repo is not a git repository"
    exit 1
}
if (-not [System.IO.Path]::IsPathRooted($hooksDir)) {
    $hooksDir = Join-Path $Repo $hooksDir
}
New-Item -ItemType Directory -Force -Path $hooksDir | Out-Null

$hookBody = @'
#!/usr/bin/env bash
# RepoGraph auto-index hook (managed by Install-GitHooks.ps1)
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

Write-Host "done. RepoGraph will auto-index $Repo on HEAD changes."
