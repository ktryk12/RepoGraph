# RepoGraph Podman Helper Script
param(
    [Parameter(Position=0)]
    [ValidateSet("up", "down", "build", "logs", "shell", "pod", "status", "clean")]
    [string]$Command = "up",

    [switch]$Dev,
    [switch]$Mounts,
    [switch]$Detached
)

$composeFile = if ($Dev) { "podman-compose.repograph.dev.yml" } else { "podman-compose.repograph.yml" }

# Base compose args; -Mounts layers in the local-source override for indexing
# repositories outside the build context.
$composeArgs = @("-f", $composeFile)
if ($Mounts) { $composeArgs += @("-f", "podman-compose.repograph.override.yml") }

switch ($Command) {
    "up" {
        $args = $composeArgs + @("up")
        if ($Detached) { $args += "-d" }
        Write-Host "[INFO] Starting RepoGraph med Podman..."
        & podman-compose @args
    }

    "down" {
        Write-Host "[INFO] Stopper RepoGraph..."
        & podman-compose @composeArgs down
    }

    "build" {
        Write-Host "[INFO] Bygger RepoGraph image..."
        & podman build -t localhost/repograph:latest -f Containerfile .
    }

    "logs" {
        & podman-compose @composeArgs logs -f
    }

    "shell" {
        Write-Host "[INFO] Åbner shell i RepoGraph API container..."
        & podman exec -it repograph-api /bin/bash
    }

    "pod" {
        Write-Host "[INFO] Starter RepoGraph som Kubernetes pod..."
        & podman play kube repograph-pod.yaml
    }

    "status" {
        Write-Host "=== PODMAN CONTAINERS ==="
        & podman ps
        Write-Host "`n=== PODMAN IMAGES ==="
        & podman images | Select-String "repograph|redis|postgres"
        Write-Host "`n=== NETWORK STATUS ==="
        try {
            $response = Invoke-WebRequest -Uri "http://localhost:8001/status" -TimeoutSec 3
            Write-Host "[OK] RepoGraph API responsive: $($response.StatusCode)"
        } catch {
            Write-Host "[ERROR] RepoGraph API not responsive: $($_.Exception.Message)"
        }
    }

    "clean" {
        Write-Host "[WARNING] Sletter alle RepoGraph containers og images..."
        $confirm = Read-Host "Er du sikker? (y/N)"
        if ($confirm -eq "y" -or $confirm -eq "Y") {
            & podman-compose @composeArgs down -v
            & podman rmi localhost/repograph:latest -f 2>$null
            & podman volume prune -f
            Write-Host "[OK] Cleanup komplet"
        }
    }

    default {
        Write-Host @"
RepoGraph Podman Helper

Usage: .\podman-repograph.ps1 [command] [options]

Commands:
  up      Start RepoGraph services
  down    Stop RepoGraph services
  build   Build RepoGraph image
  logs    Show logs
  shell   Open shell in API container
  pod     Start as Kubernetes pod
  status  Show status
  clean   Remove all RepoGraph containers/images

Options:
  -Dev        Use development configuration
  -Mounts     Layer in local-source mounts (index repos outside build context)
  -Detached   Run in background (for 'up')

Examples:
  .\podman-repograph.ps1 up -Detached
  .\podman-repograph.ps1 up -Dev
  .\podman-repograph.ps1 up -Mounts -Detached
  .\podman-repograph.ps1 shell
  .\podman-repograph.ps1 status
"@
    }
}