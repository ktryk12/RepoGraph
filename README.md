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

### Docker (anbefalet)

```bash
docker compose up -d
```

Starter API (`:8001`), Redis og Postgres. Postgres-skema oprettes automatisk ved første opstart.

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
| `POST` | `/shared-retrieval/prepare` | Fuld retrieval → komprimeret prompt til consumer |
| `POST` | `/shared-retrieval/working-set` | Returnér råt WorkingSet |
| `POST` | `/shared-retrieval/prompt-pack` | Returnér PromptPack |
| `POST` | `/shared-retrieval/retry-pack` | Pack til retry efter verificeringsfejl |
| `GET` | `/shared-retrieval/status` | Status for cache, Postgres og profiler |

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

MCP-serveren eksponerer 23 tools til Claude Code og andre MCP-kompatible clients:

**Indeksering og søgning**
- `index_repo` · `search_symbols` · `get_symbol` · `get_symbol_context` · `blast_radius` · `repo_status`

**Retrieval og kontekst**
- `prepare_task_context` — fuld pipeline: classify → retrieve → komprimér → pak
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

### MCP i Docker

```json
{
  "mcpServers": {
    "repograph": {
      "command": "docker",
      "args": ["exec", "-i", "repograph-api", "repograph-mcp"]
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

## Multi-tenant

Alle endpoints understøtter `X-Tenant-ID`-header. Hvert tenant får sin egen isolerede graph store og Redis-namespace:

```bash
curl http://localhost:8001/status -H "X-Tenant-ID: projectA"
curl http://localhost:8001/status -H "X-Tenant-ID: projectB"
```

---

## Docker

```bash
# Prod
docker compose up -d

# Dev med hot-reload
docker compose -f docker-compose.yml -f docker-compose.dev.yml up

# Kør migrationer manuelt
docker compose run --rm migrate
```

Services: `repograph-api` · `repograph-redis` · `repograph-postgres`

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
- Ingen automatisk reindeksering ved filændringer
- Ingen semantisk vector-søgning
- Ingen cross-repo graf uden eksplicit multi-tenant opsætning
