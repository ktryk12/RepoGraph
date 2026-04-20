# CLAUDE_SHARED_RETRIEVAL_BRIEF.md

> **Placering:** `RepoGraph`-repoet  
> **Formål:** Bygge et shared retrieval-lag i RepoGraph, som kan bruges af lokal **Claude Code**, **Codex**, **babyAI-agenter**, **NewModel** og andre consumers.  
> **Driftskontekst:** Kører tæt på `llm-server`, med **RepoGraph** som persistent strukturel hukommelse og **Redis** som hot cache / arbejdshukommelse.  
> **Mål:** Gøre det muligt for små eller mellemstore modeller at arbejde på store kodebaser ved hjælp af **lagdelt forståelse**, **komprimering**, **working sets** og **verifikation uden for modellen**.

---

## 1. Problem vi løser

Vi skal ikke forsøge at proppe hele kodebasen ind i modelkonteksten.

Vi skal i stedet bygge et system, hvor:

1. hele repoet forstås **uden for prompten**
2. forståelsen lagres som **struktur + summaries + traces + cache**
3. hver konkret opgave får et **task-specifikt working set**
4. Claude Code / Codex / babyAI-agenter kun får den **mindst nødvendige komprimerede kontekst**
5. nye indsigter skrives tilbage som **task-memory**, **retrieval traces** og **cache-opdateringer**

Den mentale model er:

> **Hele repoet skal være repræsenteret i systemets hukommelse, ikke i hvert enkelt prompt.**

---

## 2. Arkitekturprincipper

Disse principper er hårde krav:

1. **Small models never get whole repo**
2. **Precision before context size**
3. **Retrieval must be multi-stage**
4. **WorkingSet is a first-class contract object**
5. **TaskMemory stores workflow, not chat**
6. **Verification lives outside the model**
7. **RepoGraph is shared infrastructure, not babyAI-specific logic**
8. **Redis is hot memory, not source of truth**
9. **All new shared retrieval capabilities must work without babyAI**
10. **Backward compatibility for existing RepoGraph endpoints must be preserved**

---

## 3. Hvor koden skal bo

### Skal bygges i RepoGraph

Shared retrieval skal bo i **RepoGraph**, fordi det er generisk infrastruktur, som skal kunne bruges af:

- lokal Claude Code
- lokal Codex
- babyAI-agenter
- NewModel-pipelines
- andre repos i fremtiden

### Må ikke bo i babyAI

babyAI må gerne være consumer, men må ikke være hjemsted for den generiske retrieval/compression/hukommelses-infrastruktur.

**Tommelregel:**

> Hvis komponenten giver værdi til mere end babyAI, skal den ikke bo i babyAI.

---

## 4. Shared retrieval: målarkitektur

```text
Claude Code / Codex / babyAI-agent / NewModel consumer
                    │
                    ▼
        Shared Retrieval Gateway (i RepoGraph)
                    │
        ┌───────────┼───────────┐
        │           │           │
        ▼           ▼           ▼
   Graph store    Redis      LLM router
   + summaries    cache      (GLM/Qwen/Mixtral/...)
        │                       │
        └───────────┬───────────┘
                    ▼
          Working Set Builder
                    │
                    ▼
           Prompt/context pack
                    │
                    ▼
          Consumer model / agent
                    │
                    ▼
              Verifier layer
                    │
                    ▼
         Trace + memory + cache update
```

---

## 5. Hovedkomponenter der skal bygges

## 5.1 Shared Retrieval Gateway

Et nyt lag i RepoGraph, som er den primære entrypoint for consumers.

### Ansvar
- modtage en task/query fra consumer
- klassificere task-type
- hente relevant strukturel kontekst fra graph store
- hente varme summaries / tidligere task-state fra Redis
- kalde compression/summarization hvis nødvendigt
- bygge et task-specifikt `WorkingSet`
- returnere enten:
  - struktureret JSON
  - prompt-ready context
  - eller begge

### Output modes
- `compact`
- `structured`
- `prompt`
- `debug`

### Må ikke
- være babyAI-specifik
- være afhængig af én bestemt model
- kræve babyAI-artifacts for at fungere

---

## 5.2 Summary Pipeline (L0-L4)

Vi skal bygge eller færdiggøre en hierarkisk summary-pipeline.

### Lag
- **L0** repo summary
- **L1** service/subsystem summary
- **L2** file summary
- **L3** symbol summary
- **L4** code spans on-demand

### Krav
- summaries skal kunne genereres via LLM-router
- summaries skal kunne regenereres inkrementelt
- summaries skal kunne gemmes persistent i RepoGraph
- summaries skal caches i Redis
- summaries skal være billige at genbruge

### Minimum output per level
- `summary`
- `last_generated_at`
- `generator_model`
- `source_hash`
- `confidence`
- `token_count`

---

## 5.3 Redis Hot Memory Layer

Redis skal bruges som **arbejdshukommelse**, ikke som permanent sandhed.

### Redis skal cache
- repo summaries
- service summaries
- file summaries
- symbol summaries
- working sets
- retrieval traces
- task state
- previous patch verdicts
- verification results (kortform)
- consumer session snapshots

### Redis må ikke være eneste kopi af
- graph relations
- canonical symbol metadata
- permanent task memory
- artifacts

### Foreslåede nøgletyper
- `repo:{tenant}:{repo}:summary:l0`
- `repo:{tenant}:{repo}:service:{name}:summary`
- `repo:{tenant}:{repo}:file:{path_hash}:summary`
- `repo:{tenant}:{repo}:symbol:{symbol_id}:summary`
- `repo:{tenant}:{repo}:workingset:{query_hash}`
- `repo:{tenant}:{repo}:task:{task_id}:state`
- `repo:{tenant}:{repo}:verify:{task_id}:last`
- `repo:{tenant}:{repo}:session:{session_id}:snapshot`

### TTL-strategi
- summaries: lang TTL + invalidation ved source hash change
- working sets: medium TTL
- session state: kort TTL
- verification cache: kort/medium TTL

---

## 5.4 Working Set Builder v2

WorkingSet skal være den centrale kontrakt mellem retrieval-laget og consumeren.

### WorkingSet skal indeholde
- `task_id`
- `query`
- `task_family`
- `objective`
- `constraints`
- `repo_summary`
- `service_summaries[]`
- `file_summaries[]`
- `symbol_summaries[]`
- `code_spans[]`
- `related_tests[]`
- `related_configs[]`
- `token_budget`
- `tokens_used`
- `target_model_context`
- `explanation`
- `provenance`
- `retrieval_trace_id`

### Builderen skal kunne
- respektere token budget
- pakke forskelligt afhængigt af modeltype og task-family
- prioritere summaries over rå kode
- kun inkludere code spans on-demand
- udvide eller komprimere output afhængigt af consumerbehov

### Outputprofiler
- `tiny` for 4K-6K
- `small` for 8K-16K
- `medium` for 32K
- `patch` for minimal diff workflows
- `review` for arkitektur/review-opgaver

---

## 5.5 Compression / Context Packing Engine

Der skal bygges et eksplicit lag til at pakke kontekst til små modeller.

### Ansvar
- oversætte WorkingSet til model-egnet prompt/context
- prioritere summaries, symboler og kun få code spans
- skære støj væk
- bevare sporbarhed
- kunne lave retry-pakker efter verifier-fejl

### Strategier
- summary-first packing
- symbol-first packing
- patch-first packing
- test-first packing
- retry packing with failure reason

### Output
- prompt preamble
- task objective
- ranked context blocks
- “why included” explanations
- optional diff/retry context

---

## 5.6 Task Memory / Retrieval Trace Store

Vi skal skelne mellem:

### Retrieval trace
Kort, teknisk spor af hvordan kontekst blev fundet.

Felter:
- `retrieval_trace_id`
- `query`
- `task_family`
- `stages_executed`
- `candidates_considered`
- `symbols_selected`
- `files_selected`
- `token_budget`
- `timings_ms`
- `precision_signals`

### Task memory
Arbejdsjournal for et faktisk workflow.

Felter:
- `task_id`
- `goal`
- `hypothesis`
- `files_touched`
- `symbols_touched`
- `patches_attempted`
- `verification_history`
- `open_questions`
- `next_recommended_step`

**Vigtigt:** dette er ikke chat-historik.

---

## 5.7 Model Router Integration

Shared retrieval må ikke være låst til én model.

### Skal understøtte
- GLM 5.1
- Qwen code-varianter
- Mixtral
- fremtidig NewModel / HashLogMoE
- fallback hvis en model er nede

### Routeren skal kunne vælge model til
- summary generation
- compression/resummarization
- risk scoring
- patch assistance

### Krav
- modelvalg skal være policy- og budgetstyret
- alle modelkald skal være auditerbare
- retrieval-laget skal kunne fungere i degraded mode, hvis modelrouteren er nede

---

## 5.8 Verifier Integration

Verifier skal være et fast led i shared retrieval-laget, især for coding flows.

### Verifier skal kunne køre
- targeted tests
- lint
- type checks
- static analysis
- dependency validation
- smoke tests

### Verifier-resultater skal
- returneres til consumer
- gemmes som task-memory
- kunne bruges til retry-packing
- kunne påvirke ranking og future retrieval

---

## 5.9 Consumer Adapters

Vi skal understøtte flere consumers uden at gøre kerneinfrastrukturen custom pr. consumer.

### Adapters
- `claude_code_adapter`
- `codex_adapter`
- `babyai_agent_adapter`
- `newmodel_training_adapter`

### Adapterens ansvar
- oversætte consumerens requestformat til shared retrieval request
- vælge outputprofil
- sende relevante metadata med
- ikke indeholde kerne-retrievallogik

---

## 6. API og MCP der skal eksistere

## 6.1 Bevar eksisterende funktioner

Følgende eksisterende RepoGraph-kapabiliteter skal bevares og bruges som base:
- task classification
- coarse retrieval
- working set build
- symbol summary
- file summary
- context verification
- multi-stage retrieval

## 6.2 Nye eller udvidede REST endpoints

Foreslåede endpoints:

- `POST /shared-retrieval/prepare`
- `POST /shared-retrieval/working-set`
- `POST /shared-retrieval/prompt-pack`
- `POST /shared-retrieval/retry-pack`
- `GET /summary/repo`
- `GET /summary/service/{name}`
- `GET /summary/file/{path}`
- `GET /summary/symbol/{symbol}`
- `GET /task-memory/{task_id}`
- `POST /task-memory/update`
- `GET /retrieval-trace/{trace_id}`
- `POST /cache/invalidate`
- `GET /shared-retrieval/status`

## 6.3 Nye eller udvidede MCP tools

Foreslåede tools:

- `prepare_task_context`
- `build_prompt_pack`
- `build_retry_pack`
- `get_repo_summary`
- `get_service_summary`
- `get_task_memory`
- `update_task_memory`
- `invalidate_context_cache`

---

## 7. Request/response-kontrakter

## 7.1 Shared retrieval request

```json
{
  "repo_path": "e:/repos/NewModel",
  "query": "Refactor model routing for teacher selection",
  "task_hint": "targeted_refactor",
  "consumer": "claude_code",
  "session_id": "optional-session-id",
  "task_id": "optional-task-id",
  "target_model": "glm-5.1",
  "target_context": 6000,
  "output_profile": "patch",
  "include_debug": false
}
```

## 7.2 Shared retrieval response

```json
{
  "task_family": "targeted_refactor",
  "working_set_id": "uuid",
  "retrieval_trace_id": "uuid",
  "prompt_pack": {
    "preamble": "...",
    "objective": "...",
    "context_blocks": []
  },
  "working_set": {
    "token_budget": 6000,
    "tokens_used": 4280,
    "files": [],
    "symbols": [],
    "summaries": [],
    "code_spans": []
  },
  "verification_plan": {
    "tests": [],
    "lint": true,
    "typecheck": true
  },
  "cache": {
    "used": true,
    "keys": []
  }
}
```

---

## 8. Flow for lokal Claude/Codex

1. Claude Code eller Codex starter en opgave
2. første kald går til RepoGraph shared retrieval
3. shared retrieval klassificerer opgaven
4. graph store + summaries + Redis bruges til at bygge forståelse
5. der bygges et task-specifikt working set
6. der pakkes et prompt pack til den valgte model
7. consumeren udfører patch / analyse
8. verifier køres
9. resultater skrives tilbage til task memory + retrieval trace + Redis
10. næste iteration bruger den opdaterede komprimerede forståelse

**Vigtigt:** første opgave er ikke “læs hele repoet i prompten”.  
Første opgave er “hent systemets eksisterende komprimerede forståelse af repoet og byg et working set for den aktuelle opgave”.

---

## 9. Flow for babyAI-agenter

1. babyAI-agent modtager en coding task
2. agenten kalder RepoGraph shared retrieval
3. RepoGraph returnerer working set + prompt pack
4. babyAI vælger model via sin agent/policy-logik
5. agenten udfører task i lille scope
6. verifier køres
7. artifact-writer og task memory opdateres
8. næste subtask bygges på den nye systemforståelse

**babyAI er consumer, ikke hjemsted for shared retrieval.**

---

## 10. Flow for NewModel og andre repos

Samme shared retrieval-lag skal kunne bruges direkte mod andre repos, fx `NewModel`, uden babyAI involveret.

Det betyder:
- repo-path må være dynamisk
- tenant/repo-scope skal være tydeligt
- cache og task memory skal namespac'es pr. repo
- ingen babyAI-specifik afhængighed må eksistere i kernen

---

## 11. Prioriteret implementeringsrækkefølge

## Sprint 1: fundament
- design shared retrieval API-kontrakt
- tilføj Redis layer
- implementér cache-strategi for summaries og working sets
- eksponér repo/service/file/symbol summaries ensartet
- byg `prepare_task_context`

## Sprint 2: working set + packing
- færdiggør WorkingSet Builder v2
- tilføj token-budget enforcer
- implementér prompt packing profiler
- implementér retry packing
- persistér retrieval traces

## Sprint 3: memory + verifier kobling
- implementér task memory store
- koble verifier output til task memory
- byg feedback loop fra verification til næste retrieval
- tilføj cache invalidation ved ændrede filer

## Sprint 4: consumer adapters
- Claude Code adapter
- Codex adapter
- babyAI adapter
- NewModel adapter

## Sprint 5: hardening
- benchmarks
- latency profiling
- precision metrics
- degraded mode tests
- backward compatibility tests

---

## 12. Ikke-mål

Dette skal **ikke** bygges i første omgang:

- ingen chat memory som erstatning for task memory
- ingen fuld autonom planlægning i shared retrieval-laget
- ingen babyAI-specifik orchestration i RepoGraph
- ingen krav om NewModel for at systemet virker
- ingen antagelse om store context windows
- ingen strategi hvor hele repoet injiceres råt i prompten

---

## 13. Acceptance criteria

Shared retrieval er klar, når følgende er sandt:

1. Claude Code kan starte en opgave mod et vilkårligt repo og få et brugbart prompt pack uden at læse hele repoet råt
2. Codex kan gøre det samme via samme kerneinfrastruktur
3. babyAI kan bruge laget som ekstern capability
4. NewModel kan bruge laget uden babyAI til stede
5. WorkingSet respekterer 4K, 6K, 16K og 32K target contexts
6. summaries og working sets caches korrekt i Redis
7. verifier-feedback kan genbruges i næste iteration
8. retrieval traces og task memory persisteres
9. eksisterende RepoGraph endpoints ikke brydes
10. systemet fungerer i degraded mode, hvis Redis eller LLM-router er delvist nede

---

## 14. Konkrete instruktioner til Claude

Byg dette som **generisk shared retrieval infrastructure i RepoGraph**.

### Start her
1. læs eksisterende RepoGraph MCP/API og bevar kompatibilitet
2. definér en klar `SharedRetrievalRequest` og `SharedRetrievalResponse`
3. tilføj Redis som hot cache layer
4. implementér `prepare_task_context`
5. byg eller færdiggør L0-L3 summaries
6. implementér WorkingSet Builder v2
7. implementér prompt packing for små kontekstvinduer
8. persistér retrieval traces og task memory
9. integrér verifier-feedback
10. tilføj consumer adapters uden at forurene kernen med consumer-specifik logik

### Hårde constraints
- ingen breaking changes til eksisterende endpoints
- ingen babyAI-afhængighed i kernen
- ingen antagelse om stor modelkontekst
- al ny logik skal kunne bruges både fra lokal Claude/Codex og fra agenter

### Output vi forventer fra Claude
- arkitekturændringer og mappeplan
- datamodeller
- API routes
- Redis cache layer
- summary services
- working set builder
- prompt/context packer
- task memory store
- verifier integration
- tests
- dokumentation

---

## 15. Den ene sætning der styrer alt

> **Vi bygger ikke et system, hvor modellen skal huske hele repoet. Vi bygger et system, hvor repoet allerede er komprimeret, struktureret og genfindeligt, før modellen bliver spurgt.**

