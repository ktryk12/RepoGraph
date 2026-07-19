# RepoGraph

RepoGraph er en lokal code intelligence-platform til AI-kodningsagenter.

Den analyserer et repository med Tree-sitter, bygger en persistent vidensgraf og eksponerer grafen via REST API og MCP-server. Consumers som Claude Code, Codex, babyAI og NewModel henter strukturel kontekst fra RepoGraph — uden at RepoGraph nogensinde kalder en LLM selv.

---

## Arkitektur

```
Consumer (Claude Code / Codex / babyAI / NewModel)
  │
  ▼
RepoGraph MCP / REST API
  │
  ├─ Shared Retrieval Gateway
  │    ├─ Redis (hot cache — summaries, working sets, prompt packs)
  │    ├─ Context Compressor (LongCodeZip-inspireret, ingen LLM)
  │    └─ Prompt Packer (5 strategier)
  │
  ├─ Graph Store / CogDB (strukturel sandhed)
  │    ├─ Symboler, filer, relationer (CALLS, IMPORTS, DEFINES ...)
  │    ├─ Enrichment (risk level, signatur, service, test/entrypoint)
  │    ├─ Summaries (L0 repo → L1 service → L2 fil → L3 symbol)
  │    └─ Knowledge graph (docs, CODEOWNERS, CI-workflows)
  │
  └─ Postgres (operational store)
       ├─ retrieval_traces (token-estimater, komprimeringsmetrics)
       ├─ task_memory (patch-forsøg, test failures, precision signals)
       ├─ verifier_runs (lint, typecheck, pytest resultater)
       └─ usage_logs (model, tokens, latency)
```

RepoGraph er **rent strukturel retrieval** — ingen LLM-kald internt. Consumers genererer summaries og skriver dem tilbage via PUT-endpoints.

---

## Hvad RepoGraph kan

- Indeksere et repository med Tree-sitter (10 sprog)
- Respektere `.gitignore` under indeksering
- Gemme symboler og relationer i en embedded graf (CogDB)
- Beregne blast radius for symbolændringer
- Berige symboler med: signatur, risk level, service-tilhørsforhold, test/entrypoint-flag
- Bygge token-budget-styrede WorkingSets til AI-consumers
- Komprimere kontekst strukturelt (3 passes: drop calls → drop summaries → drop low-risk)
- Cache summaries og working sets i Redis
- Logge retrieval traces og task memory i Postgres
- Verificere patches via pytest, ruff, mypy, bandit

### Understøttede sprog

Python · TypeScript · JavaScript · Go · Rust · Java · C · C++ · C# · Ruby

---

## Kom i gang

### Podman (anbefalet)

```bash
# Med podman-compose
podman-compose -f podman-compose.repograph.yml up -d

# Med PowerShell helper
.\podman-repograph.ps1 up -Detached

# Som Kubernetes pod
podman play kube repograph-pod.yaml
```

Starter API (`:8001`), Redis og Postgres. Postgres-skema oprettes automatisk ved første opstart.

**Fordele ved Podman:**
- Rootless containers (bedre sikkerhed)
- Ingen daemon kørende konstant (færre ressourcer)
- Kubernetes-pod-kompatibel
- Docker-kompatible kommandoer

### Lokal installation

```bash
pip install ".[cache,postgres]"
repograph          # REST API på http://127.0.0.1:8001
repograph-mcp      # MCP stdio-server
repograph-migrate  # Kør Postgres-migrationer
```

Kun basispakken er påkrævet. Redis og Postgres er valgfrie og aktiveres via miljøvariabler.

---

## Miljøvariabler

| Variabel | Standard | Beskrivelse |
|---|---|---|
| `REPOGRAPH_HOST` | `127.0.0.1` | API bind-adresse |
| `REPOGRAPH_PORT` | `8001` | API port |
| `REPOGRAPH_DB_BACKEND` | `cog` | Graph backend |
| `REPOGRAPH_DB_PATH` | `.repograph` | Sti til graph store |
| `REPOGRAPH_TENANT_ID` | — | Tenant for MCP-server |
| `REPOGRAPH_REDIS_URL` | `redis://localhost:6379/0` | Redis forbindelses-URL |
| `REPOGRAPH_POSTGRES_DSN` | — | Postgres DSN (valgfri) |
| `REPOGRAPH_CACHE_TTL_SUMMARY` | `3600` | Summary TTL i sekunder |
| `REPOGRAPH_CACHE_TTL_WS` | `600` | Working set TTL i sekunder |
| `REPOGRAPH_ENABLE_WRITE_TOOLS` | — | Sæt til `1` for at eksponere de 6 filsystem-write MCP-tools (fra som standard) |
| `REPOGRAPH_API_URL` | — | Bruges af `repograph-autoindex` til at indeksere via en kørende API i stedet for in-process |
| `REPOGRAPH_AUTOINDEX` | — | `lazy` = indeksér automatisk ved brug (retrieval-kald); nul omkostning på git-operationer. Sat som default i container-images |
| `REPOGRAPH_AUTOINDEX_INTERVAL` | `30` | Min. sekunder mellem staleness-tjek pr. repo/tenant i lazy mode |

---

## Typisk workflow

```bash
# 1. Indeksér et repository
curl -X POST http://localhost:8001/index \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/repo"}'

# 2. Hent kontekst til en AI-task
curl -X POST http://localhost:8001/shared-retrieval/prepare \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/repo", "query": "refactor the auth module", "output_profile": "medium"}'

# 3. Skriv en summary tilbage (genereret af consumer)
curl -X PUT http://localhost:8001/summary/file/src/auth.py \
  -H "Content-Type: application/json" \
  -d '{"text": "Authentication module — handles JWT validation and session management."}'
```

---

## REST API — vigtigste endpoints

### Indeksering
| Method | Endpoint | Beskrivelse |
|---|---|---|
| `POST` | `/index` | Indeksér et repository |
| `GET` | `/status` | Indexeringsstatus og statistik |

### Symboler og graf
| Method | Endpoint | Beskrivelse |
|---|---|---|
| `GET` | `/symbols?q=&limit=` | Søg symboler |
| `GET` | `/symbol/{symbol}` | Hent et symbol med relationer |
| `GET` | `/blast-radius/{symbol}` | Blast radius-analyse |
| `GET` | `/file/{filepath}` | Alle symboler i en fil |

### Summaries (Redis-cachet read-through)
| Method | Endpoint | Beskrivelse |
|---|---|---|
| `GET` | `/summary/symbol/{symbol}` | Hent symbol-summary |
| `PUT` | `/summary/symbol/{symbol}` | Skriv symbol-summary (invaliderer cache) |
| `GET` | `/summary/file/{filepath}` | Hent fil-summary |
| `PUT` | `/summary/file/{filepath}` | Skriv fil-summary |
| `GET` | `/summary/service/{service}` | Hent service-summary |
| `PUT` | `/summary/service/{service}` | Skriv service-summary |
| `GET` | `/summary/repo` | Hent repo-summary |
| `PUT` | `/summary/repo` | Skriv repo-summary |

### Shared Retrieval
| Method | Endpoint | Beskrivelse |
|---|---|---|
| `POST` | `/shared-retrieval/prepare` | Fuld retrieval → consumer-specifikt retrieval-pack |
| `POST` | `/shared-retrieval/analyze-plan` | Retrieval-level breakdown af brede analyze-code forespørgsler |
| `POST` | `/shared-retrieval/working-set` | Returnér råt WorkingSet |
| `POST` | `/shared-retrieval/prompt-pack` | Returnér PromptPack |
| `POST` | `/shared-retrieval/retry-pack` | Pack til retry efter verificeringsfejl |
| `GET` | `/shared-retrieval/status` | Status for cache, Postgres og profiler |

`prompt_pack` er et retrieval-/komprimerings-artifact, ikke en universel "endelig prompt". RepoGraph ejer shared retrieval og returnerer strukturerede outputs som `working_set`, `prompt_pack`, `verification_plan`, `retrieval_trace_id` og cache-metadata. Den enkelte consumer ejer derefter sin egen endelige prompt-/message-assembly.

`consumer="claude_code"` returnerer stadig et fladt `prompt`, men response-envelope bevarer ogsÃ¥ `prompt_pack`, `working_set`, `verification_plan`, `retrieval_trace_id` og cache-metadata, sÃ¥ samme payload kan sendes videre til `llm-server` uden rekonstruktion. Det flade `prompt` er en convenience-view, ikke en erstatning for den strukturerede envelope.

`consumer="babyai_agent"` returnerer et struktureret retrieval-pack med `payload_mode="structured_retrieval_pack"` og `prompt_assembly_owner="babyai"`. Payloadet indeholder bl.a. `task_id`, `task_family`, `preamble`, `objective`, `context_blocks`, `working_set`, `verification_plan`, `retrieval_trace_id`, cache-metadata og availability-flags som `retry_pack_available` / `verification_plan_available`. RepoGraph flatten'er ikke den endelige agent-prompt for babyAI.

`consumer="newmodel"` forbliver en direkte structured consumer. Den bruger samme retrieval-pack-stil som babyAI, men markeres med `prompt_assembly_owner="newmodel"`, sÃ¥ NewModel kan konsumere RepoGraph direkte uden babyAI i loopet.

Brede forespørgsler som "analyze the code", "analyze our program" eller "understand this repo" bliver ikke pakket som én stor prompt. RepoGraph laver i stedet et retrieval-level `analysis_plan` med mindre steps som repo overview, entrypoints, high-risk files og follow-up deep dive. Planen er step-decomposition, ikke fuld autonom agent-planlægning, og RepoGraph forbliver LLM-fri strukturel retrieval.

### Task Memory
| Method | Endpoint | Beskrivelse |
|---|---|---|
| `POST` | `/memory/task` | Opret task |
| `GET` | `/memory/task/{task_id}` | Hent task (Postgres first, graph fallback) |
| `POST` | `/memory/task/update` | Opdatér precision signals og status |
| `POST` | `/memory/task/{task_id}/patch` | Log patch-forsøg |
| `POST` | `/memory/task/{task_id}/test-failure` | Log testfejl |

### Verificering og infrastruktur
| Method | Endpoint | Beskrivelse |
|---|---|---|
| `POST` | `/verify` | Kør lint/typecheck/pytest på ændrede filer |
| `POST` | `/cache/invalidate` | Slet Redis-cache for et repo |
| `GET` | `/postgres/status` | Postgres-forbindelsesstatus |
| `POST` | `/postgres/migrate` | Kør Postgres-migrationer via API |

---

## MCP Tools

MCP-serveren eksponerer som standard 24 read-only tools til Claude Code og andre MCP-kompatible clients (rent strukturel retrieval, ingen skrivning):

**Indeksering og søgning**
- `index_repo` · `search_symbols` · `get_symbol` · `get_symbol_context` · `blast_radius` · `repo_status`

**Retrieval og kontekst**
- `prepare_task_context` — fuld pipeline: classify → retrieve → komprimér → pak
- `build_analysis_plan` — retrieval-level analyze-code breakdown for brede forespørgsler
- `build_working_set` · `build_prompt_pack` · `build_retry_pack`
- `find_relevant_symbols` · `classify_task` · `multi_stage_retrieve`

**Summaries**
- `get_symbol_summary` · `get_file_summary` · `get_repo_summary` · `get_service_summary`

**Task Memory**
- `get_task_memory` · `update_task_memory`

**Verificering og cache**
- `verify_task_context` · `invalidate_context_cache`

**Notes**
- `get_notes_for_symbol` · `search_notes`

### Write-tools (deaktiveret som standard)

Serveren indeholder desuden 6 filsystem-skrivende tools — `write_file`, `update_file`, `create_document`, `create_directory`, `save_spec`, `sync_from_project`. De bryder RepoGraphs rent-strukturelle kontrakt og er **slået fra som standard**. Aktivér dem eksplicit ved at sætte:

```bash
export REPOGRAPH_ENABLE_WRITE_TOOLS=1   # eksponerer de 6 write-tools (i alt 30)
```

Lad dem være slået fra, medmindre du bevidst vil lade en consumer skrive til disken via MCP.

### MCP i Podman

Container-navnet afhænger af hvordan du starter stakken:
- `podman play kube repograph-pod.yaml` → `repograph-pod-repograph-api`
- `podman-compose -f podman-compose.repograph.yml` → `repograph-api`

Projektets `.mcp.json` bruger pod-navnet (den install-frie vej):

```json
{
  "mcpServers": {
    "repograph": {
      "command": "podman",
      "args": ["exec", "-i", "repograph-pod-repograph-api", "repograph-mcp"]
    }
  }
}
```

### MCP lokalt

```bash
export REPOGRAPH_TENANT_ID=default
repograph-mcp
```

---

## Output Profiles

Styrer token-budget og packing-strategi pr. consumer/opgave:

| Profil | Token-budget | Strategi | Brug |
|---|---|---|---|
| `tiny` | 4.096 | summary_first | Meget lille kontekst |
| `small` | 8.192 | summary_first | Standard |
| `medium` | 32.768 | symbol_first | Dybe analyser |
| `patch` | 6.000 | patch_first | Targeted patches |
| `review` | 16.384 | symbol_first | Code review |

---

## Automatisk indeksering

**Lazy mode (anbefalet — nul omkostning):** Med `REPOGRAPH_AUTOINDEX=lazy` (sat som default i container-images) tjekker RepoGraph selv staleness og re-indekserer **inde i retrieval-kaldet** — første gang en task faktisk beder om kontekst. Ingen git-hooks, ingen session-hooks, nul omkostning på git-operationer. Staleness-tjekket er throttlet (default 30s pr. repo/tenant) og best-effort, så det aldrig bryder retrieval.

**Hook-baseret (valgfrit — pre-warm):** `repograph-autoindex` kan desuden wires ind i events som clone, checkout, merge/pull, commit eller session-start, hvis grafen skal være varm *før* første retrieval. Den beregner en billig signatur (git `HEAD`, ellers nyeste fil-mtime), sammenligner med sidste indekserede tilstand og re-indekserer **kun ved faktisk ændring**. Bemærk: hooks koster et proces-spawn pr. git-operation — brug lazy mode medmindre du har brug for pre-warming.

```bash
repograph-autoindex                 # indeksér cwd hvis den er ændret
repograph-autoindex /path/to/repo   # indeksér et bestemt repo
repograph-autoindex --check         # exit 1 hvis forældet (til CI-gates)
```

**Git-hooks (auto-index ved checkout/pull/commit):**

```bash
scripts/autoindex/install-git-hooks.sh /path/to/repo     # macOS/Linux/Git Bash
scripts\autoindex\Install-GitHooks.ps1 -Repo E:\repos\my-project   # Windows
```

**Claude Code (auto-index ved session-start)** — i `.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "repograph-autoindex \"$CLAUDE_PROJECT_DIR\" --quiet" } ] }
    ]
  }
}
```

Se [docs/AUTO_INDEXING.md](docs/AUTO_INDEXING.md) for alle wiring-opskrifter (git-templates, MCP/programmatisk, CI, containere).

---

## Multi-tenant

Alle endpoints understøtter `X-Tenant-ID`-header. Hvert tenant får sin egen isolerede graph store og Redis-namespace:

```bash
curl http://localhost:8001/status -H "X-Tenant-ID: projectA"
curl http://localhost:8001/status -H "X-Tenant-ID: projectB"
```

---

## Podman

```bash
# Prod
podman-compose -f podman-compose.repograph.yml up -d
# eller med helper:
.\podman-repograph.ps1 up -Detached

# Dev med hot-reload
podman-compose -f podman-compose.repograph.yml -f podman-compose.repograph.dev.yml up
# eller:
.\podman-repograph.ps1 up -Dev

# Som Kubernetes pod
podman play kube repograph-pod.yaml

# Kør migrationer manuelt
podman-compose -f podman-compose.repograph.yml run --rm migrate
```

**Services:** `repograph-api` · `repograph-redis` · `repograph-postgres`

**Helper kommandoer:**
- `.\podman-repograph.ps1 status` — kontrollér at alt kører
- `.\podman-repograph.ps1 logs` — se logs
- `.\podman-repograph.ps1 shell` — åbn shell i API container

---

## Udvikling

```bash
pip install -e ".[dev,cache,postgres]"
python -m pytest tests -q
```

---

## Hvad RepoGraph ikke gør

- Ingen LLM-kald internt — consumers genererer summaries og skriver dem tilbage
- Ingen web-UI
- Ingen indbygget reindeksering ved filændringer (men `repograph-autoindex` + git/session-hooks giver automatisk indeksering ved clone/checkout/pull/session-start — se ovenfor)
- Ingen semantisk vector-søgning
- Ingen cross-repo graf uden eksplicit multi-tenant opsætning
