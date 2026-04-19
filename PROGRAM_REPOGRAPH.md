# PROGRAM_REPOGRAPH.md

> **Dette dokument kører fra RepoGraph-repoet.** Claude Code eksekverer via RepoGraph's egen orchestration (MCP-server + egen planner + indexer). Eksekvering sker fra working directory `e:/repos/RepoGraph/` eller tilsvarende.
>
> **RepoGraph er standalone.** Et selvstændigt infrastructure-værktøj der kan leveres til babyAI, NewModel, eller tredjepart. Konsumerer babyAI-events som empirisk input når tilgængeligt — aldrig som krav.

---

## 1. Hvad er RepoGraph

RepoGraph er en **working-set motor for små kontekstvinduer**. Forvandler en kodebase fra "gigantisk tekstblob" til "navigérbar struktur" så små modeller (6000 token-kontekst) kan arbejde på kæmpe repos uden at miste overblik.

Kernekonceptet:
> Precision before context size

Opnås via:
- Hierarkiske summaries (L0 repo → L1 service → L2 file → L3 symbol → L4 code span)
- 9 task-familier med specialiserede retrieval-strategier
- WorkingSet som first-class objekt
- Multi-stage retrieval (classify → coarse → structural → fine)
- Patch/verifier loop udenfor model
- Task-memory som arbejdsjournal

Designet til:
- Små modeller (6K-32K kontekst)
- Kæmpe repos (100K+ noder)
- Agent-orchestrering (babyAI)
- Code-intelligence (NewModel inference)
- Tredjeparts AI-coding-tools

---

## 2. Nuværende tilstand (2026-04-18)

### 2.1 Hvad er på plads

- 46.136 noder indekseret
- MCP-server aktiv og responderer
- Grundlæggende graf-queries: symbols, calls, imports, blast radius
- Node-typer: file, symbol, function, class
- Edge-typer: CALLS, IN_FILE, IMPORTS
- Storage: graph-backend (70 MB store)
- SDK/client til brug fra andre programmer

### 2.2 Hvad mangler

- REST API (bygges i dette program, Sprint 9)
- Hierarkiske summaries (L0-L3)
- Node-berigelse (signature, doc_summary, service_name, risk_level osv.)
- Relation-typer: TESTS, CONFIGURES, ENTRYPOINT_FOR, BELONGS_TO_SERVICE m.fl.
- TaskPlanner service
- 9 task-familier
- WorkingSet-builder
- StructuralExpander
- CodeSpanSelector
- PatchVerifier
- TaskMemory store
- Knowledge graph ekspansion (tests, configs, docs, ADRs, runbooks, ownership, CI history, issue history)

---

## 3. Arkitekturel vision

RepoGraph er **en specialist-hjerne for retrieval**. Modellen skal være specialist på lille scope — RepoGraph bærer struktur, retrieval og verifikation.

### 3.1 Nordstjerne pipeline

```
User task
    ↓ Task Planner (klassificer task-familie)
    ↓ RepoGraph Retriever (multi-stage)
    ↓ Working Set Builder (pakker kontekst)
    ↓ Specialist Model (foreslår løsning)
    ↓ Patch/Test Generator
    ↓ Verifier (tests, lint, types, static analysis)
    ↓ Memory Updater (arbejdsjournal)
```

### 3.2 Hard rules

1. Små modeller får aldrig hele repoet
2. Retrieval skal være flerstadie, ikke enkeltkald
3. WorkingSet er first-class objekt
4. Verifikation ligger uden for modellen
5. TaskMemory gemmer arbejdsforløb, ikke chat

### 3.3 Summary-hierarki

- **L0:** Repo overview (1 per repo)
- **L1:** Service/subsystem overview (1 per service)
- **L2:** File summary (1 per fil)
- **L3:** Symbol summary (1 per symbol)
- **L4:** Code spans (on-demand, kun når ændringer skal forstås)

---

## 4. Standalone-garanti

RepoGraph kører uafhængigt. Konkret:

| Integration | Tilgængelig | Ikke tilgængelig |
|---|---|---|
| babyAI skill-events | Bruges som empirisk input til retrieval-design | Design baseret på repo-topologi alene |
| babyAI artifact-store | Ekstra context for summaries | Summaries genereres fra kode alene |
| NewModel for summary-generation | Lokal, billig summarization | Brug LLM-gateway (GLM 5.1, Qwen, Mixtral) |
| NewModel for inference | Kode-intelligent retrieval-konsumer | Eksporter til andre konsumere (babyAI, tredjepart) |
| External consumers | Ekstra værdi-spor | Intern brug er tilstrækkelig |

**Minimum viable RepoGraph:** Grafen + hierarkiske summaries + WorkingSet-builder + 3 task-familier. Nok til at levere værdi til én consumer.

---

## 5. Roadmap

### 5.1 Fase 1: Node-berigelse + relationer (uge 1-2)

Udvid graf fra "syntaks-graf" til "arbejdsrelevans-graf". Nye node-felter: signature, short_summary, service_name, test_refs, config_refs, risk_level. Nye edge-typer: TESTS, CONFIGURES, ENTRYPOINT_FOR, BELONGS_TO_SERVICE, DEPENDS_ON_RUNTIME, IMPLEMENTS_INTERFACE, MENTIONED_IN_DOC.

### 5.2 Fase 2: Hierarkiske summaries (uge 3-4)

SummaryBuilder producerer repo_summary.json, service_summaries.json, file_summaries.json, symbol_summaries.json. Gemmes som noder eller sidecar-data.

### 5.3 Fase 3: Multi-stage retrieval (uge 5-6)

TaskPlanner, coarse retriever, structural expander, code span selector. Retrieval-trace persisteres.

### 5.4 Fase 4: WorkingSet Builder (uge 7-8)

First-class WorkingSet-objekt. Token-budget-enforcer. Compression strategies. Explanation layer.

### 5.5 Fase 5: Task-familier (uge 9-10)

9 task-familier: symbol_lookup, file_to_symbol_map, bug_localization, call_chain_reasoning, blast_radius_analysis, targeted_refactor, test_impact_lookup, targeted_test_generation, config_dependency_reasoning.

### 5.6 Fase 6: Patch-loop + Task memory (uge 11-12)

Minimal-patch-specialist prompts. TaskMemoryRecord store. Previous patches tracker. Test failure tracker.

### 5.7 Fase 7: Verifier-lag (uge 13-14)

PatchVerifier orchestrator. Targeted test runner, lint, type check, static analysis, dependency validator, smoke tests. Verification-to-memory feedback.

### 5.8 Fase 8: Programming knowledge graph (uge 15-16)

Udvid graf: tests, configs, docs, ADRs, runbooks, ownership, CI failures, issue history.

### 5.9 Fase 9: API + MCP + Benchmarks (uge 17-18)

REST endpoints (10 nye), MCP tools (6 nye), benchmark suite, SWE-bench adaptation, success metrics dashboard.

---

## 6. Claude Code eksekveringsprotokol

### 6.1 Forudsætninger

Claude Code kører i RepoGraph's repo-kontekst. Emit til RepoGraph's eget orchestration-system:

```json
{
  "intent_type": "program_precondition_check",
  "program": "REPOGRAPH",
  "requester": "claude-code",
  "checks": [
    "graph_store_accessible",
    "mcp_server_running",
    "minimum_triples_count_10k",
    "indexer_operational",
    "teacher_available_for_summary_generation",
    "python_env_active",
    "repograph_test_suite_passing"
  ]
}
```

**Stop hvis check fejler:**

| Fejlet check | Action |
|---|---|
| `graph_store_accessible` | Verificer storage-backend (graphrepo.db, MSSQL eller tilsvarende) |
| `mcp_server_running` | Genstart MCP-server |
| `minimum_triples_count_10k` | Kør `python scripts/index_repo.py` eller tilsvarende indekser |
| `teacher_available_for_summary_generation` | Kræver mindst én LLM-gateway-endpoint (port 80 eller direkte til GLM 5.1/Qwen/Mixtral) |
| `indexer_operational` | Fix indexer før opgradering |

### 6.2 Bootstrap

```json
{
  "intent_type": "execute_program",
  "program": "REPOGRAPH",
  "active_phase": "<vælg: node_enrichment | summaries | multi_stage_retrieval | working_set | task_families | patch_memory | verifier | knowledge_graph | api_mcp>",
  "integration_mode": "opportunistic",
  "empirical_input_source": "babyai_skill_events_if_available",
  "degradation_policy": "full_functionality_without_external_deps",
  "hard_rules": [
    "small_models_never_get_whole_repo",
    "retrieval_must_be_multi_stage",
    "working_set_is_first_class_object",
    "verification_lives_outside_model",
    "task_memory_stores_workflow_not_chat"
  ],
  "target_model_contexts": [4096, 6000, 16384, 32768],
  "preserve_existing_endpoints": true,
  "requester": "claude-code",
  "approval_required": true
}
```

### 6.3 Task-loop

**Task-types per fase:**

**Fase 1 (node-berigelse):**
- `empirical_retrieval_analysis`, `node_field_schema_extension`, `relation_type_registry`, `graph_migration_script`, `service_name_resolver`, `test_refs_indexer`, `config_refs_indexer`, `risk_level_heuristic`.

**Fase 2 (summaries):**
- `summary_builder_scaffold`, `repo_summary_generator`, `service_summary_generator`, `file_summary_generator`, `symbol_summary_generator`, `summary_persistence_layer`, `summary_refresh_cadence`, `teacher_routing_for_summaries`.

**Fase 3 (multi-stage retrieval):**
- `task_planner_service`, `task_type_classifier_model`, `coarse_retriever_impl`, `structural_expander_impl`, `code_span_selector_impl`, `retrieval_trace_persistence`.

**Fase 4 (working set):**
- `working_set_pydantic_model`, `working_set_builder_service`, `working_set_serializer`, `working_set_token_budget_enforcer`, `working_set_compression_strategies`, `working_set_explanation_layer`.

**Fase 5 (task-familier):**
- `task_family_symbol_lookup`, `task_family_file_to_symbol_map`, `task_family_bug_localization`, `task_family_call_chain_reasoning`, `task_family_blast_radius_analysis`, `task_family_targeted_refactor`, `task_family_test_impact_lookup`, `task_family_targeted_test_generation`, `task_family_config_dependency_reasoning`, `task_family_registry_service`.

**Fase 6 (patch + memory):**
- `patch_loop_orchestrator`, `minimal_patch_specialist_prompts`, `task_memory_schema`, `task_memory_store`, `task_memory_recall_api`, `previous_patches_tracker`, `test_failure_tracker`.

**Fase 7 (verifier):**
- `patch_verifier_service`, `targeted_test_runner`, `lint_runner`, `type_check_runner`, `static_analysis_runner`, `dependency_validator`, `smoke_test_runner`, `verification_result_schema`, `verification_to_memory_feedback`.

**Fase 8 (knowledge graph):**
- `test_node_indexer`, `config_node_indexer`, `docs_node_indexer`, `adr_parser`, `runbook_indexer`, `ownership_metadata_indexer`, `ci_failure_history_indexer`, `issue_history_indexer`, `knowledge_graph_relationship_enricher`.

**Fase 9 (API + MCP):**
- 10 REST endpoints, 6 MCP tools, benchmark suite, SWE-bench adaptation, success metrics dashboard.

### 6.4 Integration-opdagelse (opportunistisk)

Ved relevante tasks:

```json
{
  "intent_type": "integration_discovery",
  "look_for": [
    "babyai_skill_events_available",
    "babyai_artifact_store_readable",
    "newmodel_hashlogmoe_endpoint_available"
  ]
}
```

**Hvis babyAI-events tilgængelig:** Brug som empirisk input til Fase 1 (hvilke noder tilgås mest) og Fase 3 (task-klassifikation).

**Hvis NewModel tilgængelig:** Brug som billig summary-generator. Ellers brug LLM-gateway.

**Hvis ingen:** Design baseret på repo-topologi alene. Summaries genereres via LLM-gateway.

### 6.5 Backward compatibility

Eksisterende endpoints bevares:
- `GET /symbols`
- `GET /symbol/{id}`
- `GET /file/{path}`
- `GET /blast-radius/{symbol}`

Nye endpoints tilføjes som nyt lag i Fase 9. Ingen breaking changes.

### 6.6 Telemetri

Hver retrieval-operation emitter:

```json
{
  "event_type": "repograph.retrieval.completed",
  "retrieval_id": "uuid",
  "task_type": "...",
  "stages_executed": ["classify", "coarse", "structural", "fine"],
  "stage_timings_ms": {...},
  "working_set_produced": {
    "token_count": N,
    "symbols_count": N,
    "files_count": N
  },
  "consumer_model": "...",
  "precision_signals": {
    "consumer_accepted": true|false,
    "patch_applied": true|false,
    "verification_passed": true|false
  }
}
```

---

## 7. Eskaleringspunkter

Claude Code må IKKE beslutte selvstændigt:

| Situation | Kategori |
|---|---|
| Breaking changes til eksisterende endpoints | `backward_compat_decision_needed` |
| Summary-generering koster markant GPU-tid | `compute_budget_approval` |
| Graf-migration for 46k+ noder | `migration_strategy_decision` |
| Task-familie-afgrænsninger (flere end 9?) | `scope_decision_needed` |
| Verifier-politik strikthed | `verification_policy_decision` |
| Knowledge graph scope-udvidelse | `scope_decision_needed` |
| Benchmark-valg (intern vs. SWE-bench vs. begge) | `benchmark_decision` |
| Performance-regression >20% | `performance_regression_alert` |

---

## 8. Succes-metrics

| Metric | Target |
|---|---|
| Tokens per task (gennemsnit) | Reducér >50% vs. flad retrieval |
| Retrieval precision (relevante/totale noder) | >80% |
| Irrelevante filer i prompt | <10% |
| Multi-file task success rate | Forbedres >30% |
| Iterationsloops før korrekt patch | <3 i 80% af tilfælde |
| Bugfix + test update success | >90% |
| Existing endpoint latency regression | <10% |
| Summary generation kost per repo | <€50 per fuld regenerering |

---

## 9. Afslutningskriterier per fase

**Fase 1-2 complete når:** Nye noder + summaries indekseret for fuld repo.
**Fase 3-4 complete når:** Multi-stage retrieval + WorkingSet fungerer E2E.
**Fase 5-6 complete når:** 9 task-familier + patch-loop + memory operational.
**Fase 7-8 complete når:** Verifier + knowledge graph aktivt.
**Fase 9 complete når:** API + MCP + benchmarks grønne.

Hele programmet er "produktion-ready" når alle 9 faser complete + backward compat verificeret + benchmarks passerer.

---

## 10. Integration points — se `INTEGRATION_POINTS.md`

Alt om hvordan RepoGraph samarbejder med babyAI og NewModel er i den delte kontrakt-fil.

---

## 11. Principper

1. **RepoGraph er selvstændigt.** Leverer værdi uden andre programmer.
2. **Precision before context size.** Skarpere retrieval, ikke større prompt.
3. **Hierarkier vinder over flad search.** L0 → L1 → L2 → L3 → L4.
4. **WorkingSet er kontrakten.** Struktureret objekt, ikke tekstblob.
5. **Specialist på lille scope.** Modellen har få opgaver, ikke mange.
6. **Verifikation er uden for modellen.** Tests, lint, types, static analysis.
7. **Empiri over teori.** Design-beslutninger baseret på faktiske retrieval-mønstre.
8. **Bevar bagud-kompatibilitet.** Eksisterende consumers må ikke brydes.
9. **TaskMemory er arbejdsjournal.** Ikke chat, beslutningsspor.
10. **9 task-familier er nok.** Ikke mere før empiri kræver det.
11. **Benchmark offentligt.** SWE-bench-inspireret, reproducerbart.

---

## 12. Kontekst-budget for Claude Code

- Per task-event: max 2K tokens
- Per deliverable: max 1500 tokens
- Samlet workflow per task: max 4K tokens
- Memory mellem tasks: nul (state er i graph-store + task-memory)

---

## 13. Start-kommando

**Første meddelelse til Claude Code (i RepoGraph repo):**

> "Kør PROGRAM_REPOGRAPH.md. Start med afsnit 6.1 (precondition_check). Vælg aktiv fase fra afsnit 6.2 — standardvalg er node_enrichment hvis ikke andet specificeret. Task-loop fra afsnit 6.3. Integration-discovery efter afsnit 6.4. Bevar backward compat. Kør indtil fase complete eller stop-signal."

---

*RepoGraph PROGRAM · 2026-04-18 · Standalone · Opportunistic integration · Eksekveres fra RepoGraph-repo*
