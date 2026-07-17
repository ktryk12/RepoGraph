# PROGRAM_BABYAI.md

> **Dette dokument kører fra babyAI-repoet.** Claude Code eksekverer via babyAI's egen orchestrator (planner + Kafka + policy-validator + artifact-writer). Eksekvering sker fra working directory `e:/repos/babyAI/` eller tilsvarende.
>
> **babyAI er standalone.** Kræver ingen af de andre programmer (NewModel, RepoGraph) for at fungere. Konsumerer output fra dem OPPORTUNISTISK når tilgængeligt — aldrig som krav.

---

## 1. Hvad er babyAI

babyAI er et **agent-netværk** der kan:

- Kode (mikroservices, frontend, scripts, full-stack)
- Skrive tekster (artikler, rapporter, bøger, dokumentation)
- Producere film og video (via claude-video + video-pipeline)
- Håndtere foto og visuel content (via ComfyUI + image-service)
- Faktatjekke påstande (Truth Engine)
- Handle finansielle instrumenter (via BrokerAdapter-laget)
- Publicere content på TikTok, YouTube, LinkedIn, Twitter
- **Lære sig selv nye færdigheder** styret af constitution-policy

Netværket er sammensat af:
- **Mikroservices** (14+ FastAPI-services på Docker)
- **Agent-flåde** (orchestrator, supervisor, specialister, editorial council)
- **Skill-system** (SKILL.md-baseret, komponerbart)
- **Event-bus** (Kafka som koordinerings-substrate)
- **Policy-lag** (constitution v2 + domain policies)
- **Hukommelse** (memory-plane med embeddings + provenance)

babyAI bruger LLM-serverens modeller som "motor". PT mange modeller (GLM 5.1, Qwen-serien, Codestral, Mixtral osv.), men målet er at NewModel på sigt overtager mere og mere af inference.

---

## 2. Nuværende tilstand (2026-04-18)

### 2.1 Hvad er landet

**Fra Sprint 1 af tidligere revenue-program:**
- Constitution v2 + domain policies (fact_check + trading)
- SkillBootstrapper + SKILL.md som kanonisk format
- Video-pipeline bootstrap (4 agenter + 4 skills)
- Publisher med OAuth-flows + TokenManager (LinkedIn, YouTube, TikTok, Twitter)
- BrokerAdapter ABC + KeyVault (HashiCorp Vault)
- JSON Schema 2020-12 pattern for events
- 46.136 noder i RepoGraph

**Mikroservices kørende:**
- context-plane (8092), tool-runtime (8093), artifact-writer, request-gate, truthpack-conversation, planner, policy-bootstrap, memory-plane, orchestrator-worker, ui, publisher, claude-video, voice-runtime (7080) med flere.

**Modeller tilgængelige via LLM-gateway (port 80):**
- Always-on: GLM 5.1 (8211), Qwen3-30B-code (8207), Mixtral (8105)
- On-demand: 27 modeller samlet

### 2.2 Hvad mangler

- Skill-runtime: bygget men ikke startet som docker-service
- Claim-detector: ikke bygget
- Fact-check agenter (fact_check_agents/): ikke bygget
- Broker-gateway: interface klar, ingen adapters
- Revenue-programmets Sprint 2-7: ikke startet
- Self-improvement loops (nightly retro, auto-cso): ikke aktiveret

---

## 3. Arkitekturel vision

babyAI er **emergent i sit output, styret i sit grundlag**. Kernen er:

1. **Intent-drevet.** Alt starter som et `decision.intent`-event. Planner nedbryder, orchestrator eksekverer.
2. **Policy-gated.** Ingen produktion uden at passere policy-validator.
3. **Artifact-persistent.** Alt output går gennem artifact-writer. Fuld provenance.
4. **Opportunistisk.** Konsumerer NewModel + RepoGraph når tilgængeligt. Fungerer også uden.
5. **Selv-lærende.** Skill-execution events + failure-patterns + user feedback opdaterer agenterne.

---

## 4. Standalone-garanti

babyAI kører uafhængigt af NewModel og RepoGraph. Konkret:

| Integration-point | Uden NewModel | Uden RepoGraph |
|---|---|---|
| Model-inference | Bruger LLM-gateway (GLM, Qwen, Mixtral) som nu | Påvirker ikke |
| Retrieval | Påvirker ikke | Bruger context-plane's eksisterende search |
| Træningsdata | Events strømmer til Kafka — hvis NewModel konsumerer senere, fint | Påvirker ikke |
| Working-sets | Påvirker ikke | Agent-prompts bygges med flad retrieval (mindre effektivt men fungerer) |

**Degraded-mode er ikke "crippled mode".** babyAI leverer fuld værdi alene. Integrationerne er opgraderinger, ikke forudsætninger.

---

## 5. Roadmap

### 5.1 Det babyAI skal levere standalone

**Spor A: Skill-runtime (4 uger)**
Genererer skill.execution.completed events. Det er både babyAI's egen dev-værktøjs-tjeneste OG (som bi-produkt) træningsdata for NewModel hvis den senere konsumerer. Men babyAI bygger det ikke *for* NewModel — den bygger det for sig selv.

**Spor B: Fact-check / Truth Engine (8 uger)**
Komplet revenue-spor. Claim-detector → truthpack → fact-check agents → verdict → legal_review → claude-video → publisher. Producerer videoer automatisk på TikTok, YouTube, Instagram.

**Spor C: Trading Execution (revenue Fase 2)**
BrokerAdapter færdig, Binance/Bybit adapters, order-manager, risk-engine. BYOK-model. 

**Spor D: Content Production**
Bøger, artikler, long-form content via editorial_council + journalist_agent.

**Spor E: Data/API Marketplace (revenue Fase 3)**
Trust-score API, fact-check-datasets, anonymiserede trade-flows.

Hvert spor er selvstændigt. De kan startes i vilkårlig rækkefølge. De bruger samme underlæggende skill-runtime + policy + artifact-writer.

### 5.2 Det babyAI får som bonus når andre programmer modnes

- NewModel klar → babyAI kan ruteflere opgaver til NewModel (dansk-first, lokal, billigere)
- RepoGraph opgraderet → agent-prompts bliver markant bedre på små kontekster

Men babyAI kan leveres til kunder I DAG med de modeller der er tilgængelige.

---

## 6. Claude Code eksekveringsprotokol

### 6.1 Forudsætninger

Claude Code kører i babyAI's repo-kontekst. Emit til babyAI's Kafka:

```json
{
  "intent_type": "program_precondition_check",
  "program": "BABYAI",
  "requester": "claude-code",
  "checks": [
    "kafka_available",
    "artifact_writer_reachable",
    "policy_validator_v2_active",
    "constitution_loaded",
    "orchestrator_worker_running",
    "llm_gateway_responding_port_80",
    "minimum_three_always_on_teachers"
  ]
}
```

Topic: `decision.intent`

**Ingen external checks** (ikke NewModel, ikke RepoGraph). Hvis de er oppe og responder på integration-calls, godt. Hvis ikke, fortsæt.

### 6.2 Bootstrap

```json
{
  "intent_type": "execute_program",
  "program": "BABYAI",
  "active_tracks": ["skill_runtime", "fact_check", "trading_execution", "content_production", "data_api"],
  "selected_track": "<vælg: skill_runtime | fact_check | trading | content | data_api>",
  "integration_mode": "opportunistic",
  "newmodel_integration": "auto_detect_if_available",
  "repograph_integration": "auto_detect_if_available",
  "degradation_policy": "full_functionality_without_external_deps",
  "requester": "claude-code",
  "approval_required": true
}
```

### 6.3 Task-loop

Claude Code modtager tasks via `decision.requested`. Task-types per spor:

**Skill-runtime tasks** (se tidligere SKILL_RUNTIME_PROGRAM).

**Fact-check tasks:**
- `claim_detector_scaffold`, `claim_detector_scanners`, `fact_check_agents_build`, `video_template_factcheck`, `review_queue_factcheck_ui`, `publisher_hardening_oauth`, `publisher_rate_limit`, `factcheck_e2e_smoke`.

**Trading tasks:**
- `binance_adapter_build`, `bybit_adapter_build`, `paper_adapter_wrapper`, `order_manager_service`, `risk_engine_service`, `execution_audit_daemon`, `trading_ui`, `stripe_billing`, `trading_soak_test`.

**Content production tasks:**
- `editorial_council_wiring`, `long_form_template`, `book_outline_generator`, `claude_video_book_trailer`, `publication_scheduler`.

**Data/API tasks:**
- `data_exporter_service`, `trust_api_service`, `rate_limiter_middleware`, `partnership_material_generator`.

### 6.4 Integration-opdagelse (opportunistisk)

Ved hver task-start, Claude Code spørger:

```json
{
  "intent_type": "integration_discovery",
  "look_for": ["newmodel_hashlogmoe_available", "repograph_working_set_builder_available"]
}
```

Hvis NewModel findes og svarer på `/v1/models` med `hashlogmoe-13b`: brug den til relevante task-types. Hvis ikke: brug GLM 5.1.

Hvis RepoGraph har working-set-builder endpoint: brug det til at pakke context. Hvis ikke: brug simpel søgning.

**Ingen task blokerer på integration.** Hvis integration er nede midt i en task: fallback automatisk.

### 6.5 Policy-validering

Hver deliverable passerer `policy-validator`:
- Constitution v2
- `policy/domain/fact_check/*.yaml` (hvis fact-check-task)
- `policy/domain/trading/*.yaml` (hvis trading-task)
- `policy/domain/content_production/*.yaml` (hvis content-task)

3 revisions per task. Ved 3 afvisninger: eskalering.

### 6.6 Telemetri

Hver task emitter:

```json
{
  "event_type": "babyai.task.completed",
  "task_id": "uuid",
  "track": "fact_check | trading | content | data_api | skill_runtime",
  "deliverable_artifact_id": "...",
  "integration_used": {
    "newmodel_called": true|false,
    "repograph_working_set_used": true|false,
    "teachers_used": [...]
  },
  "provenance": {...}
}
```

Hvis NewModel konsumerer disse events senere som træningsdata: fint. Men babyAI genererer dem til sit eget audit-spor primært.

---

## 7. Eskaleringspunkter

| Situation | Kategori |
|---|---|
| Ny skill-familie der ikke matcher eksisterende policy | `policy_expansion_needed` |
| Revenue-spor kræver eksternt partner (licens, kunde) | `business_decision_needed` |
| Content-produktion som er juridisk grænse-tilfælde | `legal_review_required` |
| Trading-execution mod live-konti | `regulatory_approval_required` |
| Integration med NewModel giver dårligere resultat end GLM 5.1 | `integration_regression_alert` |

---

## 8. Succes-metrics

babyAI-programmet måles på:

| Metric | Mål |
|---|---|
| Skill-executions per uge | >500 efter måned 2 |
| Fact-check videos published | >100/uge efter måned 3 |
| Paying customers across alle spor | >20 efter måned 6 |
| MRR | >50.000 kr efter måned 9 |
| Agent-fejlprocent (tasks der kræver manuel intervention) | <15% |
| Policy rejection rate | <5% |
| Integration-opportunism (brug af NewModel/RepoGraph når tilgængelig) | >80% |

---

## 9. Afslutningskriterier per spor

Hvert spor har egen "complete"-definition. Hele babyAI-programmet er aldrig "færdigt" — det er et **produkt i kontinuerlig udvikling**.

Spor betragtes som produktion-ready når:

- Alle core services er deployed + sund
- Soak-test 7 dage uden incidents
- Policy rejection rate <5%
- User-facing UI på plads
- Billing aktiveret (for revenue-spor)

---

## 10. Integration points — se `INTEGRATION_POINTS.md`

Alt om hvordan babyAI samarbejder med NewModel og RepoGraph findes i den delte kontrakt-fil. babyAI implementerer sin side af kontrakten. De andre programmer implementerer deres.

---

## 11. Principper

1. **babyAI er selvstændigt.** Kan levere fuld værdi uden andre programmer.
2. **Opportunistisk integration.** Brug NewModel/RepoGraph når tilgængelige, ignorer når ikke.
3. **Policy er lov.** Alt valideres. Ingen undtagelser.
4. **Events er kontrakten.** Alt auditérbart gennem Kafka + artifact-writer.
5. **Hvert spor er selvstændigt.** Skill-runtime, fact-check, trading, content, data/api kan startes og stoppes uafhængigt.
6. **Human-in-the-loop for irreversible handlinger.** Trading execution, publishing, merges — altid approval.
7. **Self-improvement gennem events.** Hver execution opdaterer systemets viden.

---

## 12. Kontekst-budget for Claude Code

- Per task-event: max 2K tokens
- Per deliverable: max 1500 tokens
- Samlet workflow per task: max 4K tokens
- Memory mellem tasks: nul (state er i artifact-writer)

---

## 13. Start-kommando

**Første meddelelse til Claude Code (i babyAI repo):**

> "Kør PROGRAM_BABYAI.md. Start med afsnit 6.1 (precondition_check). Vælg aktivt spor fra afsnit 6.2 — standardvalg er skill_runtime hvis ikke andet specificeret. Task-loop fra afsnit 6.3. Integration-discovery efter afsnit 6.4 for hver task. Kør indtil stop-signal eller spor færdig."

---

*babyAI PROGRAM · 2026-04-18 · Standalone · Opportunistic integration · Eksekveres fra babyAI-repo*