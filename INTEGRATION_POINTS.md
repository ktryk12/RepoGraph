# INTEGRATION_POINTS.md

> **Delt kontrakt mellem babyAI, NewModel og RepoGraph.** Dette er ikke et program — det er en specifikation. Hver af de tre programmer implementerer sin side af kontrakten. Ingen af dem kræver at andre eksisterer.
>
> **Alle integrations er opportunistiske.** Hvis en integration-endpoint er nede, falder programmet tilbage til standalone-mode uden crash.
>
> **Versioner:** Denne kontrakt er `v1.0`. Breaking changes kræver ny version. Programmer skal annoncere hvilken version de understøtter i integration_discovery.

---

## 1. Delte Kafka-topics

### 1.1 Topics babyAI producerer

| Topic | Schema | Konsumeres af |
|---|---|---|
| `skill.execution.completed` | `skill_execution_schema_v1` | NewModel (valgfrit), RepoGraph (valgfrit) |
| `skill.execution.started` | `skill_execution_started_v1` | RepoGraph (telemetri) |
| `skill.user_feedback` | `skill_feedback_v1` | NewModel (quality signals) |
| `babyai.artifact.created` | `artifact_ref_v1` | Alle |
| `babyai.policy.violation` | `policy_violation_v1` | Alle (audit) |
| `factcheck.verdict.published` | `verdict_v1` | NewModel (training data, transformativ) |
| `trading.signal.generated` | `trading_signal_v1` | NewModel (science expert data) |

### 1.2 Topics NewModel producerer

| Topic | Schema | Konsumeres af |
|---|---|---|
| `newmodel.iteration.completed` | `newmodel_iteration_v1` | babyAI (kan opgradere til ny model), RepoGraph (ny inference-target) |
| `newmodel.candidate.proposed` | `newmodel_candidate_v1` | babyAI (admin-notification) |
| `newmodel.promotion.completed` | `newmodel_promotion_v1` | Alle (model-version-bump) |
| `newmodel.metrics.published` | `newmodel_metrics_v1` | babyAI, RepoGraph (quality-dashboard) |

### 1.3 Topics RepoGraph producerer

| Topic | Schema | Konsumeres af |
|---|---|---|
| `repograph.retrieval.completed` | `retrieval_v1` | babyAI (precision tracking), NewModel (training signal) |
| `repograph.working_set.built` | `working_set_v1` | babyAI (agent-prompts), NewModel (inference-context) |
| `repograph.summary.updated` | `summary_v1` | babyAI (cache invalidation) |
| `repograph.index.refreshed` | `index_refresh_v1` | Alle (graph-state notification) |

---

## 2. Delte event-schemas

### 2.1 `skill_execution_schema_v1`

```json
{
  "event_id": "uuid",
  "timestamp": "ISO-8601",
  "skill_name": "string",
  "skill_version": "semver",
  "triggered_by": "user|agent|kafka|cron",
  "trigger_context": {},
  "input": {
    "user_prompt": "string",
    "parameters": {}
  },
  "context_pack": {
    "repograph_working_set_id": "string|null",
    "policy_context": {},
    "memory_context": {}
  },
  "prompts_used": [
    {"role": "system|user|assistant", "content": "string"}
  ],
  "model_used": "string",
  "output": {
    "raw": "string",
    "structured": {}
  },
  "outcome": {
    "status": "success|failure|partial",
    "artifacts_produced": [],
    "findings_count": "number",
    "auto_fixes_applied": "number"
  },
  "quality_signals": {
    "user_accepted": "boolean|null",
    "user_feedback": "string|null",
    "policy_violations": []
  },
  "metrics": {
    "duration_seconds": "number",
    "tokens_input": "number",
    "tokens_output": "number",
    "cost_usd": "number|null"
  },
  "provenance": {
    "git_commit": "string",
    "request_trace_id": "string",
    "related_artifact_ids": []
  }
}
```

### 2.2 `working_set_v1`

```json
{
  "working_set_id": "uuid",
  "task_id": "string",
  "task_type": "string",
  "query": "string",
  "objective": "string",
  "constraints": [],
  "repo_nodes": [],
  "services": [],
  "files": [],
  "symbols": [],
  "summaries": {
    "L0_repo": "string|null",
    "L1_service": {},
    "L2_file": {},
    "L3_symbol": {}
  },
  "code_spans": [],
  "related_tests": [],
  "related_configs": [],
  "risks": [],
  "retrieval_trace": [],
  "token_budget": "number",
  "tokens_used": "number",
  "target_model_context": "number"
}
```

### 2.3 `newmodel_iteration_v1`

```json
{
  "iteration_id": "uuid",
  "timestamp": "ISO-8601",
  "iteration_type": "daily_delta|weekly_candidate|monthly_full|quarterly_publish",
  "dataset_manifest_ref": "artifact_id",
  "newmodel_config_used": "string",
  "teachers_used": [],
  "samples_count": "number",
  "language_distribution": {},
  "expert_distribution": {},
  "copyright_audit": {
    "passed": "boolean",
    "sources_used": [],
    "rejected_samples": "number"
  },
  "metrics_before": {},
  "metrics_after": {
    "top1": "number",
    "top2": "number",
    "anchor_recall_at_10": "number",
    "expert_collapse_score": "number",
    "router_fallback_rate": "number"
  },
  "release_gate_status": "RED|YELLOW|GREEN",
  "promotion_decision": "promoted|rejected|pending_user_approval",
  "candidate_checkpoint_path": "string"
}
```

### 2.4 `retrieval_v1`

```json
{
  "retrieval_id": "uuid",
  "timestamp": "ISO-8601",
  "task_type": "string",
  "query": "string",
  "stages_executed": [],
  "stage_timings_ms": {},
  "working_set_produced": {
    "working_set_id": "uuid",
    "token_count": "number"
  },
  "consumer_model": "string",
  "target_context": "number",
  "precision_signals": {
    "consumer_accepted": "boolean",
    "patch_applied": "boolean",
    "verification_passed": "boolean"
  }
}
```

---

## 3. Delte HTTP-endpoints

### 3.1 babyAI eksponerer

| Endpoint | Method | Formål |
|---|---|---|
| `/babyai/artifacts/{id}` | GET | Hent artifact-indhold |
| `/babyai/artifacts/search` | POST | Find artifacts efter kind, date, owner |
| `/babyai/skills/registry` | GET | List tilgængelige skills |
| `/babyai/skills/{name}/execute` | POST | Trigger skill-execution |
| `/babyai/policy/validate` | POST | Validér payload mod constitution |

### 3.2 NewModel eksponerer

| Endpoint | Method | Formål |
|---|---|---|
| `/v1/models` | GET | List tilgængelige modeller (inkl. hashlogmoe-13b) |
| `/v1/completions` | POST | Standard completion-API |
| `/v1/chat/completions` | POST | Chat-API |
| `/newmodel/metrics/latest` | GET | Seneste quality metrics |
| `/newmodel/trace/{id}` | GET | Hent trace-entry |
| `/newmodel/candidates/pending` | GET | Liste candidates afventer promotion |

### 3.3 RepoGraph eksponerer

| Endpoint | Method | Formål |
|---|---|---|
| `GET /symbols` | GET | (bevaret) Liste symboler |
| `GET /symbol/{id}` | GET | (bevaret) Symbol-details |
| `GET /file/{path}` | GET | (bevaret) Fil-details |
| `GET /blast-radius/{symbol}` | GET | (bevaret) Blast radius |
| `POST /task/classify` | POST | Klassificer task-familie |
| `POST /retrieve/coarse` | POST | Find relevante services/filer/symboler |
| `POST /retrieve/structural` | POST | Udvid med callers, tests, configs |
| `POST /working-set/build` | POST | Hele retrieval-kæden → WorkingSet |
| `GET /summary/repo` | GET | L0 |
| `GET /summary/service/{id}` | GET | L1 |
| `GET /summary/file/{path}` | GET | L2 |
| `GET /summary/symbol/{id}` | GET | L3 |
| `POST /verify/patch-plan` | POST | Pre-execution verifikation |
| `POST /memory/task/update` | POST | TaskMemory-opdatering |

For `POST /shared-retrieval/prepare` med `consumer="claude_code"` må response gerne indeholde et fladt `prompt`, men den må ikke miste `prompt_pack`, `working_set`, `verification_plan`, `retrieval_trace_id` eller `cache`. Det flade `prompt` er kun et kompatibilitetslag oven på den fulde envelope.

---

## 4. MCP tools

### 4.1 babyAI eksponerer

- `execute_skill`
- `fetch_artifact`
- `validate_policy`
- `list_skills`

### 4.2 NewModel eksponerer

- `query_hashlogmoe`
- `get_model_metrics`
- `list_trained_checkpoints`

### 4.3 RepoGraph eksponerer

- `classify_task`
- `find_relevant_symbols`
- `build_working_set`
- `get_symbol_summary`
- `get_file_summary`
- `blast_radius`
- `verify_task_context`

---

## 5. Integration-discovery protokol

Hvert program tjekker andres tilgængelighed ved bootstrap og ved hver relevant task. Discovery er **non-blocking** — hvis en integration er nede, fortsæt i degraded mode.

### 5.1 Discovery-request

```json
{
  "intent_type": "integration_discovery",
  "requester_program": "babyai|newmodel|repograph",
  "contract_version": "v1.0",
  "look_for": [
    "babyai_artifact_store_readable",
    "babyai_kafka_available",
    "babyai_skill_events_flowing",
    "newmodel_hashlogmoe_endpoint_available",
    "newmodel_training_pipeline_active",
    "repograph_mcp_server_running",
    "repograph_working_set_builder_available",
    "repograph_summaries_available"
  ]
}
```

### 5.2 Discovery-response

```json
{
  "response_id": "uuid",
  "available_integrations": {
    "babyai_artifact_store_readable": true,
    "babyai_kafka_available": true,
    "babyai_skill_events_flowing": true,
    "newmodel_hashlogmoe_endpoint_available": false,
    "newmodel_training_pipeline_active": false,
    "repograph_mcp_server_running": true,
    "repograph_working_set_builder_available": false,
    "repograph_summaries_available": false
  },
  "contract_versions_supported": {
    "babyai": "v1.0",
    "newmodel": null,
    "repograph": "v0.9"
  }
}
```

### 5.3 Degraded mode rules

| Integration manglende | Fallback-adfærd |
|---|---|
| `newmodel_hashlogmoe_endpoint` | Brug LLM-gateway (port 80) til inference |
| `repograph_working_set_builder` | Brug simpel flad retrieval |
| `babyai_skill_events_flowing` | Design-beslutninger baseret på statisk analyse |
| `babyai_artifact_store_readable` | Gem lokalt, sync senere |
| Contract version mismatch | Annoncér i log, brug kompatibel subset |

---

## 6. Delte artifact-kinds

Alle programmer bruger babyAI's `artifact-writer` som fælles store når tilgængelig. Artifacts har påkrævet `owning_program`-tag.

### 6.1 Artifact kinds

| kind | Ejer | Beskrivelse |
|---|---|---|
| `skill_execution_log` | babyai | Fuld skill-run-historik |
| `fact_check_verdict` | babyai | Verdict + evidens + kilder |
| `trading_decision` | babyai | Trade-intent + outcome |
| `content_production` | babyai | Publiceret content |
| `newmodel_checkpoint` | newmodel | Model-snapshot-ref |
| `newmodel_dataset_manifest` | newmodel | Dataset-bygge-manifest |
| `newmodel_training_trace` | newmodel | Training-run-trace |
| `newmodel_model_card` | newmodel | HF-kompatibel model card |
| `repograph_summary_set` | repograph | L0-L3 summaries snapshot |
| `repograph_working_set` | repograph | Konkret WorkingSet |
| `repograph_retrieval_trace` | repograph | Retrieval-beslutningsspor |

### 6.2 Cross-reference

Artifacts kan referere andre artifacts via `related_artifact_ids`. Eksempel: En `fact_check_verdict` fra babyAI kan referere en `repograph_working_set` + `newmodel_checkpoint` der blev brugt.

---

## 7. Fælles policy-scope

Hver program har egne policies, men følgende er **delte konventioner** der gælder alle tre:

### 7.1 Constitution-baserede fælles rules

- Ingen skrivning uden artifact-writer
- ECB-gating for eksterne kald
- Schema-validering af alle Kafka-events
- Per-sentence provenance for al ML-data
- Safe mode default, unsafe kræver approval

### 7.2 Delte policy-filer

Lokaliseret i babyAI under `policy/shared/`:

- `policy/shared/audit_requirements.yaml`
- `policy/shared/provenance_standards.yaml`
- `policy/shared/copyright_baseline.yaml`
- `policy/shared/safety_baseline.yaml`

NewModel og RepoGraph læser disse via HTTP-fetch fra babyAI eller lokalt spejlede kopier. Ingen tvang — men hvis de overholdes, kan alle tre programmer interoperere sikkert.

---

## 8. Versionering

### 8.1 Semver på kontrakt

Denne fil versioneres semver:
- **MAJOR:** Breaking changes i schemas eller endpoints
- **MINOR:** Nye topics/endpoints/fields, bagud-kompatible
- **PATCH:** Dokumentations-fixes, ingen adfærdsændring

Nuværende: `v1.0`

### 8.2 Programmer annoncerer understøttelse

Hvert program eksponerer sin understøttelse via discovery:

```json
{
  "program": "babyai",
  "contract_version_supported": "v1.0",
  "contract_version_min_required": "v1.0"
}
```

### 8.3 Upgrade-procedure

Breaking changes (v2.0, v3.0) kræver koordineret opgradering:
1. Én version publiceret (v1.0 + v2.0 understøttes parallelt 30 dage)
2. Programmer opgraderer én efter én
3. v1.0 deprecated efter alle programmer er på v2.0

Under overgangen kører discovery-protokollen med `contract_version_supported` for at vælge rigtig schema.

---

## 9. Strategic direction

Kontrakten er designet så følgende evolution er mulig uden breaking changes:

### 9.1 NewModel overtager gradvist babyAI's inference

Som HashLogMoE-13B modnes, kan babyAI rutere flere opgaver til den:
- Skill-execution → fra GLM 5.1 til HashLogMoE hvis relevant expert matcher
- Fact-check verdict generation → fra GLM 5.1 til HashLogMoE creative_other expert
- Dansk legal_review → fra GLM 5.1 til HashLogMoE danish_general expert

Routing-beslutning er babyAI's, baseret på NewModel metrics. Ingen tvang.

### 9.2 RepoGraph bliver shared infrastructure

WorkingSet-builder kan eksponeres for tredjeparts-brug:
- Commercial API til AI-coding-tools
- Open source-release til forskning
- White-label til enterprise

babyAI og NewModel er bare to konsumere blandt mange.

### 9.3 Alle tre bliver produkter

- babyAI: SaaS agent-platform for virksomheder
- NewModel: HuggingFace-publiceret model + commercial licensing
- RepoGraph: API-tjeneste + on-prem licensing

Kontrakten gør det muligt at sælge dem separat eller bundlet.

---

## 10. Operationelle regler

1. **Opportunistisk integration.** Ingen program kræver andre. Alle fallback'er gracefully.
2. **Discovery ved bootstrap + før relevante tasks.** Cache resultater 5 min, refresh ved fejl.
3. **Non-blocking kald.** Timeout 30s på integration-calls. Ved timeout → degraded mode.
4. **Event-first, ikke request-response.** Hvor muligt, brug Kafka frem for HTTP.
5. **Audit-first.** Alle integration-kald logges til artifact-writer hvis tilgængelig.
6. **Versionér eksplicit.** Altid annoncér contract-version i discovery.
7. **Breaking changes koster.** Overvej MINOR eller workaround før MAJOR.
8. **Deprecation-periode 30 dage.** Gamle schemas understøttes parallelt.
9. **Fælles policy-baseline.** Alle tre følger shared policies.
10. **Ingen skjulte afhængigheder.** Alle integrationer dokumenteres her.

---

*INTEGRATION_POINTS · v1.0 · 2026-04-18 · Shared contract mellem babyAI, NewModel, RepoGraph · Ingen binding afhængigheder — kun opportunistiske berigelser*
