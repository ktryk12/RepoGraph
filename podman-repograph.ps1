# RepoGraph Podman Helper Script
param(
    [Parameter(Position=0)]
    [ValidateSet("up", "down", "build", "migrate", "logs", "shell", "pod", "status", "clean")]
    [string]$Command = "up",

    [switch]$Dev,
    [switch]$Mounts,
    [switch]$Build,
    [switch]$Detached
)

$podmanCommand = Get-Command podman -ErrorAction SilentlyContinue
$podmanExe = if ($podmanCommand) {
    $podmanCommand.Source
} else {
    Join-Path $env:ProgramFiles "RedHat\Podman\podman.exe"
}
if (-not (Test-Path $podmanExe)) {
    throw "Podman blev ikke fundet. Installér Podman Desktop eller tilføj podman.exe til PATH."
}

$composeCommand = Get-Command podman-compose -ErrorAction SilentlyContinue
$composeExe = if ($composeCommand) { $composeCommand.Source } else { $null }
$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue

function Invoke-Compose {
    param([string[]]$ComposeArguments)
    if ($composeExe) {
        & $composeExe @ComposeArguments
    } elseif ($pythonLauncher) {
        & $pythonLauncher.Source -3.13 -m podman_compose @ComposeArguments
    } else {
        & $podmanExe compose @ComposeArguments
    }
}

function Resolve-ContainerName {
    param([string[]]$Candidates)
    foreach ($candidate in $Candidates) {
        & $podmanExe container exists $candidate
        if ($LASTEXITCODE -eq 0) { return $candidate }
    }
    return $Candidates[0]
}

# Base compose args; -Mounts layers in the local-source override for indexing
# repositories outside the build context.
$composeArgs = @("-f", "podman-compose.repograph.yml")
if ($Dev) { $composeArgs += @("-f", "podman-compose.repograph.dev.yml") }
if ($Mounts) { $composeArgs += @("-f", "podman-compose.repograph.override.yml") }

switch ($Command) {
    "up" {
        $args = $composeArgs + @("up")
        if ($Build) { $args += "--build" }
        if ($Detached) { $args += "-d" }
        Write-Host "[INFO] Starting RepoGraph med Podman..."
        Invoke-Compose -ComposeArguments $args
    }

    "down" {
        Write-Host "[INFO] Stopper RepoGraph..."
        Invoke-Compose -ComposeArguments ($composeArgs + @("down"))
    }

    "build" {
        Write-Host "[INFO] Bygger RepoGraph image..."
        & $podmanExe build --target runtime -t localhost/repograph:latest -f Containerfile .
    }

    "migrate" {
        Write-Host "[INFO] Kører alle manglende RepoGraph migrationer..."
        $apiContainer = Resolve-ContainerName @("repograph-api", "repograph-pod-repograph-api")
        & $podmanExe exec $apiContainer repograph-migrate
    }

    "logs" {
        Invoke-Compose -ComposeArguments ($composeArgs + @("logs", "-f"))
    }

    "shell" {
        Write-Host "[INFO] Åbner shell i RepoGraph API container..."
        $apiContainer = Resolve-ContainerName @("repograph-api", "repograph-pod-repograph-api")
        & $podmanExe exec -it $apiContainer /bin/sh
    }

    "pod" {
        Write-Host "[INFO] Bygger og erstatter RepoGraph Kubernetes pod..."
        & $podmanExe build --target runtime -t localhost/repograph:latest -f Containerfile .
        if ($LASTEXITCODE -eq 0) {
            & $podmanExe play kube --replace repograph-pod.yaml
        }
    }

    "status" {
        Write-Host "=== PODMAN CONTAINERS ==="
        & $podmanExe ps
        Write-Host "`n=== PODMAN IMAGES ==="
        $imageList = & $podmanExe images
        $imageList | Select-String "repograph|redis|postgres"
        Write-Host "`n=== NETWORK STATUS ==="
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:8001/status" -TimeoutSec 3
            Write-Host "[OK] RepoGraph API responsive: $($response.StatusCode)"
        } catch {
            Write-Host "[ERROR] RepoGraph API not responsive: $($_.Exception.Message)"
        }
        Write-Host "`n=== DATABASE MIGRATIONS ==="
        $postgresContainer = Resolve-ContainerName @("repograph-postgres", "repograph-pod-postgres")
        & $podmanExe exec $postgresContainer psql -U repograph -d repograph -c "SELECT name, applied_at FROM _schema_migrations ORDER BY name;"
    }

    "clean" {
        Write-Host "[WARNING] Sletter alle RepoGraph containers og images..."
        $confirm = Read-Host "Er du sikker? (y/N)"
        if ($confirm -eq "y" -or $confirm -eq "Y") {
            Invoke-Compose -ComposeArguments ($composeArgs + @("down", "-v"))
            & $podmanExe rmi localhost/repograph:latest -f 2>$null
            & $podmanExe volume prune -f
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
  migrate Apply pending Postgres migrations
  logs    Show logs
  shell   Open shell in API container
  pod     Start as Kubernetes pod
  status  Show status
  clean   Remove all RepoGraph containers/images

Options:
  -Dev        Use development configuration
  -Mounts     Layer in local-source mounts (index repos outside build context)
  -Build      Rebuild images before compose up
  -Detached   Run in background (for 'up')

Examples:
  .\podman-repograph.ps1 up -Detached
  .\podman-repograph.ps1 up -Build -Detached
  .\podman-repograph.ps1 up -Dev
  .\podman-repograph.ps1 up -Mounts -Detached
  .\podman-repograph.ps1 shell
  .\podman-repograph.ps1 status
"@
    }
}
