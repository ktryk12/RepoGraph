RepoGraph – samlet arkitektur med Redis, Postgres og kontekstkomprimering
0. Formål
Dette dokument samler den eksisterende RepoGraph + Redis + Postgres‑implementeringsplan med et ekstra lag for kontekstkomprimering (LongCodeZip‑inspireret) og den allerede etablerede nginx/model‑manager LLM‑infrastruktur.

Målet er:

at gøre RepoGraph til fælles retrieval‑ og kontekstplatform for Claude Code / Codex, babyAI, NewModel m.fl.,

at placere Redis og Postgres rigtigt i arkitekturen (cache vs. operational store),

at minimere LLM‑tokenforbrug via strukturel retrieval + kontekstkomprimering + caching,

uden at bryde eksisterende API/MCP‑kontrakter.

1. Overordnet målarkitektur
1.1 Hovedkomponenter
RepoGraph/cog (graph store) – strukturel sandhed for kodegrafen (symboler, filer, relationer, blast radius, summaries, working sets).

Redis (hot cache) – cached summaries, cached working sets, cached prompt packs, retry packs og kortlivet session state.

Postgres (operational store) – task memory, patch‑forsøg, test failures, retrieval traces, usage logs, verifier runs, metrics og audit.

Shared retrieval‑lag – fælles gateway/API, der henter data fra alle tre lag, bygger working sets, komprimerer kontekst og pakker prompts.

Context compressor (LongCodeZip‑inspireret) – særligt modul i shared retrieval, der reducerer konteksten til et givent tokenbudget.

nginx + model‑manager + LLM‑servere – fælles LLM‑gateway med flere kode‑ og general‑modeller bag /models/llm/by-id/*.

1.2 Højniveau dataflow
Lokal udvikling (Claude Code / Codex)
text
Claude Code / Codex
  -> RepoGraph MCP / API
  -> Shared retrieval (tenant-aware)
    -> Redis cache (summaries, working sets, prompt packs)
    -> Postgres (task memory, traces, usage, verifier runs)
    -> RepoGraph graph store (cog)
    -> Context compressor (LongCodeZip-inspireret)
    -> Prompt packer
  -> LLM-server(e) via nginx + model-manager
babyAI / NewModel runtime
text
babyAI / NewModel agent
  -> RepoGraph shared retrieval
    -> Redis
    -> Postgres
    -> RepoGraph graph store
    -> Context compressor
    -> Prompt packer
  -> LLM-server(e) via nginx + model-manager
Alle lag og services er tenant‑aware (f.eks. babyai, newmodel, llmserver).

2. Arkitekturprincipper (uændret, men udvidet)
2.1 Det der skal bevares
Graph store (cog) er fortsat strukturel sandhed for kodegrafen.

Eksisterende REST‑endpoints og MCP‑kontrakter bevares.

Working set‑koncept, summary‑hierarki og verifier‑lag bevares.

Multi‑tenant‑modellen per projekt/repo bevares.

2.2 Ny ansvarssplit (lag)
A. Graph store (RepoGraph / cog)

symboler, filer, relationer (CALLS, IMPORTS, DEFINES, ...)

blast radius, strukturel retrieval, enrichment tæt på grafen

summaries som graph‑nær viden

working‑set‑bygning

B. Redis (hot cache)

cached repo/service/file/symbol summaries

cached working sets (evt. i already‑compressed form)

cached prompt packs og retry packs

kortlivet session state

cache‑invalidation

ikke source of truth

C. Postgres (operational store)

task memory og historie

patch attempts, test failures

retrieval traces inkl. token_budget/estimates

usage logs (input/output tokens, latency, model_id)

verifier runs

metrics/dashboards, audit metadata, scheduler/job‑metadata

ikke code graph store

D. Context compressor (nyt lag)

modtager “for meget” kandidat‑kontekst fra shared retrieval

reducerer til aftalt token‑budget pr. task_family/profil

LongCodeZip‑inspireret for kode (funktions‑/region‑niveau + tokenbudget‑minimering)

skriver sine token‑estimater til Postgres (retrieval_traces)

3. Shared retrieval + kontekstkomprimering
3.1 Basalt flow
Shared retrieval består fortsat af:

context_builder – multi‑stage retrieve, working set, symbol/file‑selection

cache_policy – kigger i Redis for summaries, working sets, prompt packs

context_compressor (NY) – token‑budget‑styret trimming/komprimering

prompt_packer – endelig prompt (system + user + context + task‑memory)

gateway – MCP/API‑entrypoint, der er tenant‑aware og backend‑uafhængig

3.2 Context_compressor‑rollen
context_compressor skal:

Modtage:

query

task_family

token_budget

kandidat‑regions (filer/symboler/regions fra RepoGraph + summaries)

tenant‑info og evt. retrieval_id

Beregne:

pre_compress_token_estimate (før trimming)

udvælge/vægte regioner (LongCodeZip‑lignende strategi for kode)

post_compress_token_estimate (efter trimming)

Returnere en CompressedContext, der holder sig inden for token_budget.

Logge metrics i retrieval_traces i Postgres (inkl. strategi og savings).

Den kan implementeres med LongCodeZip som intern motor eller en egen AST/graph‑baseret variant.

4. Redis: cache for summaries, working sets og prompt packs
4.1 Summary‑cache
Keys:

tenant:{tenant}:summary:repo

tenant:{tenant}:summary:service:{service}

tenant:{tenant}:summary:file:{filepath}

tenant:{tenant}:summary:symbol:{symbol}

Read‑through:

cache hit → returnér direkte

cache miss → læs fra graph store, skriv til Redis, returnér

Invalidation:

på PUT /summary/*

på større graph refresh/sync

4.2 Working set / prompt pack‑cache
Keys:

tenant:{tenant}:working_set:{hash}

tenant:{tenant}:prompt_pack:{hash}

tenant:{tenant}:retry_pack:{hash}

Hash inkluderer:

query, task_family, token_budget, tenant

summary/graph‑version

context_compression‑strategy

Shared retrieval kalder altid cache først og falder tilbage til full build, hvis cache miss.

5. Postgres: task memory, traces, usage, verifier
5.1 Task memory
Tabeller:

task_memory – pr. task: query, family, working_set_id, retrieval_id, status, flags, timestamps.

task_patches – patch‑forsøg og resultater.

task_patch_symbols – symboler påvirket af en given patch.

task_test_failures – testfejl pr. task.

Routes:

POST /memory/task, POST /memory/task/update, GET /memory/task{...}

MCP tools: get_task_memory, update_task_memory, build_retry_pack, prepare_task_context.

5.2 Retrieval traces (inkl. tokens)
Tabeller:

retrieval_traces – retrieval_id, tenant_id, query, task_family, token_budget, token_estimate, duration_ms, consumer_model, persisted_at.

retrieval_trace_stages – per stage: navn, duration, metadata.

retrieval_trace_files – hvilke filer blev brugt.

retrieval_trace_symbols – hvilke symboler blev brugt.

Udvidelser til kontekstkomprimering:

compressor_strategy

pre_compress_tokens_estimate

post_compress_tokens_estimate

evt. compression_ratio

5.3 Usage logs
usage_logs – model_id, capability, latency, input_tokens, output_tokens, routed_at osv.

Bruges til dashboards, routing‑tuning og cost/performance‑analyse.

5.4 Verifier runs
verifier_runs – pr. verifier run: tenant, task_id, repo_path, filer/symboler, steps, passed, result_json, duration.

Bruges til precision‑signaler, dashboards og retry‑strategier.

6. MCP og API: backend‑uafhængigt lag
6.1 Storage composition
StorageServices‑containeren bruges som central entry:

python
StorageServices(
    graph=GraphRepository(...),
    cache=CacheRepository(...),
    task_memory=TaskMemoryRepository(...),
    traces=RetrievalTraceRepository(...),
    usage=UsageRepository(...),
    verifier_runs=VerifierRunRepository(...),
)
API‑routes (repograph/api/routes.py) bruger nu StorageServices i stedet for at kalde graph store direkte.

MCP‑server (repograph/mcp_server/server.py) kalder shared retrieval service‑layer, som igen bruger StorageServices.

6.2 Shared retrieval tools
Eksisterende:

classify_task, find_relevant_symbols, build_working_set, get_symbol_summary, get_file_summary, verify_task_context, multi_stage_retrieve.

Nye/udvidede:

prepare_task_context – nu med context_compressor + Redis/Postgres.

build_prompt_pack – bruger komprimeret context + task history/verifier.

build_retry_pack – bygger retry‑prompt på baggrund af Postgres‑historik.

get_repo_summary, get_service_summary – via Redis‑cache.

get_task_memory, update_task_memory – Postgres.

invalidate_context_cache – explicit cache‑invalidation.

7. Integration med nginx + model‑manager
Den nuværende nginx‑konfiguration er allerede en reverse proxy, der mapper eksterne ruter til model‑manager og videre til forskellige modeller (general, mamba, danish, code, codestral, code-fast, code-aurora, code-instruct, code-python osv.).

Vigtigt: context_compressor og shared retrieval kører før kaldet rammer nginx/model‑manager. Fra LLM‑siden ser det bare ud som kortere/mere fokuserede prompts.

7.1 Routing‑eksempler
/code og /codestral → code‑specialiserede modeller via model‑manager (til kodeopgaver).

/general, /conversation → generalist‑modeller.

/mamba, /danish → særlige modeller (mamba‑arkitektur, dansk sprog).

Shared retrieval‑lag kan pr. task_family/profil vælge passende model endpoint (f.eks. code vs. general) og sætte passende token_budget for context_compressor.

7.2 Konkrete profiler
Eksempler på profiler:

task_family = code_refactor

model: /code-fast eller /codestral

højere token_budget (kode tåler mere kontekst)

task_family = explanation

model: /general

lavere token_budget

task_family = test_fix

model: /code eller /code-python

medium/høj token_budget men stærkt filtreret på relevante test/implementation‑symboler.

8. Faseopdelt implementering (kombineret)
Fase 1 – Postgres uden at bryde retrieval
Byg Postgres‑lag (db, models, migrations).

Implementér TaskMemoryRepository, UsageRepository, VerifierRunRepository, RetrievalTraceRepository i Postgres.

Peg memory/usage/metrics/verifier‑routes om til Postgres.

Fase 2 – Redis summary‑cache
Implementér Redis‑client + summary‑cache.

Tilpas GET/PUT /summary/* til read‑through + invalidation.

Integrér i shared ret