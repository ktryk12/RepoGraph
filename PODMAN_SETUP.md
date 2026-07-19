# RepoGraph Podman Setup Guide

RepoGraph er nu konverteret fra Docker til **Podman** for bedre sikkerhed og performance.

---

## 🎯 **Hvad er ændret**

### Oprettede filer:
- `Containerfile` — erstatter Dockerfile (Podman-native)
- `podman-compose.repograph.yml` — production setup
- `podman-compose.repograph.dev.yml` — development med hot-reload
- `repograph-pod.yaml` — Kubernetes pod definition til `podman play kube`
- `podman-repograph.ps1` — PowerShell helper script
- `.containerignore` — erstatter .dockerignore
- `podman-compose.repograph.override.yml` — lokale kilde-mounts (indeksér repos udenfor build-context)

### Delte filer:
- `docker/postgres/init.sql` — Postgres init-script (mappenavnet er beholdt da det også bruges af øvrige monorepo-services)

---

## ⚙️ **Podman Setup (Windows)**

### 1. Installer podman-compose
```powershell
pip install podman-compose
# Hvis Scripts-mappen ikke er på PATH, virker helper-scriptet stadig via:
py -3.13 -m podman_compose --version
```

### 2. Initialiser Podman Machine
```powershell
podman machine init
podman machine start
```

### 3. Verificér installation
```powershell
podman --version
podman machine list
```

---

## 🚀 **Start RepoGraph**

### Metode 1: PowerShell Helper (anbefalet)
```powershell
# Production
.\podman-repograph.ps1 up -Build -Detached

# Development med hot-reload
.\podman-repograph.ps1 up -Dev -Build

# Status og logs
.\podman-repograph.ps1 status
.\podman-repograph.ps1 logs

# Shell adgang
.\podman-repograph.ps1 shell
```

### Metode 2: podman-compose
```powershell
# Production
podman-compose -f podman-compose.repograph.yml up -d

# Development
podman-compose -f podman-compose.repograph.yml -f podman-compose.repograph.dev.yml up
```

### Metode 3: Kubernetes Pod
```powershell
# Build først
podman build --target runtime -t localhost/repograph:latest -f Containerfile .

# Start som pod
podman play kube --replace repograph-pod.yaml
```

---

## 🔧 **Fejlfinding**

Ved hver containerstart venter `repograph-start` på Postgres, anvender alle
manglende migrationer og starter derefter API'et. Det gør både nye og
eksisterende Postgres-volumes kompatible med den aktuelle RepoGraph-version.

Kontrollér migrationerne:

```powershell
.\podman-repograph.ps1 migrate
podman exec repograph-postgres psql -U repograph -d repograph -c "SELECT * FROM _schema_migrations ORDER BY name;"
```

### "Cannot connect to Podman"
```powershell
podman machine init
podman machine start
podman system connection list
```

### "podman-compose command not found"
```powershell
pip install podman-compose
# eller brug direkte podman kommandoer
```

### Kontrollér services
```powershell
podman ps
podman logs repograph-api
curl http://localhost:8001/status
```

---

## ✨ **Fordele ved Podman**

| Feature | Docker | Podman |
|---|---|---|
| **Sikkerhed** | Root daemon | Rootless containers |
| **Performance** | Daemon overhead | Ingen daemon |
| **Kubernetes** | Ekstra tools | Native pod support |
| **Kompatibilitet** | Docker CLI | Docker + Kubernetes CLI |
| **Windows** | WSL2/Hyper-V | WSL2/Hyper-V |

---

## 📋 **Migration Checklist**

- ✅ Containerfile oprettet (multi-stage build)
- ✅ podman-compose.yml filer
- ✅ Kubernetes pod definition
- ✅ PowerShell helper script
- ✅ README opdateret med Podman instruktioner
- ✅ .containerignore konfiguration
- ✅ Postgres init SQL inkluderet i pod
- ✅ Alle services (API + Redis + Postgres)
- ✅ Development og production modes
- ✅ MCP integration bevaret

---

## 🔄 **Lokale repos (indeksering udenfor build-context)**

For at indeksere repositories der ligger uden for RepoGraphs build-context,
mountes de read-only via override-filen:

```powershell
# Med helper:
.\podman-repograph.ps1 up -Mounts -Detached

# Eller direkte:
podman-compose -f podman-compose.repograph.yml -f podman-compose.repograph.override.yml up -d
```

Tilpas host-stierne i `podman-compose.repograph.override.yml` til din maskine.

---

## 🎯 **Næste skridt**

1. **Installer podman-compose**: `pip install podman-compose`
2. **Initialiser machine**: `podman machine init && podman machine start`
3. **Test setup**: `.\podman-repograph.ps1 status`
4. **Start RepoGraph**: `.\podman-repograph.ps1 up -Detached`

RepoGraph er nu klar til brug med Podman! 🚀
