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

### Bevarede filer:
- `docker-compose.repograph.yml` — til bagudkompatibilitet
- `docker/postgres/init.sql` — bruges af både Docker og Podman

---

## ⚙️ **Podman Setup (Windows)**

### 1. Installer podman-compose
```powershell
pip install podman-compose
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
.\podman-repograph.ps1 up -Detached

# Development med hot-reload
.\podman-repograph.ps1 up -Dev

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
podman build -t localhost/repograph:latest -f Containerfile .

# Start som pod
podman play kube repograph-pod.yaml
```

---

## 🔧 **Fejlfinding**

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

## 🔄 **Bagudkompatibilitet**

Docker filer er bevaret som backup:
```powershell
# Hvis Podman ikke virker, brug stadig Docker:
docker compose -f docker-compose.repograph.yml up -d
```

---

## 🎯 **Næste skridt**

1. **Installer podman-compose**: `pip install podman-compose`
2. **Initialiser machine**: `podman machine init && podman machine start`
3. **Test setup**: `.\podman-repograph.ps1 status`
4. **Start RepoGraph**: `.\podman-repograph.ps1 up -Detached`

RepoGraph er nu klar til brug med Podman! 🚀