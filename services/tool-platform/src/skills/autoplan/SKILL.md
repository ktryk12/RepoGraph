---
skill_id: autoplan
name: autoplan
version: 1.0.0
description: >
  Automatisk generering af implementeringsplan fra en feature-beskrivelse eller user story.
  Triggers: "lav en plan", "autoplan", "planlæg dette", "break down opgaven",
  "hvad skal der til for at bygge", "lav implementation plan".
  Genererer faser, tasks, afhængigheder og estimater.
domains: [planning, architecture, code]
dimensions: [plan-generation, task-breakdown, estimation]
triggers:
  - lav en plan
  - autoplan
  - planlæg dette
  - break down opgaven
  - hvad skal der til for at bygge
  - lav implementation plan
expert_routing:
  model: code-codestral
  danish_version: true
  temperature: 0.3
  max_tokens: 3000
babyai_conventions:
  respect:
    - artifact-writer er eneste skrive-vej
    - ECB-gating for nye ports/adapters
    - Kafka-topics navngives med dot-notation
  flag:
    - planer der mangler test-strategi
    - planer der omgår artifact-writer
    - tasks uden klare acceptance criteria
telemetry:
  emit_events:
    - skill.autoplan.started
    - skill.autoplan.completed
---

## Formål

Generer en struktureret implementeringsplan fra en feature-beskrivelse.
Planen skal være direkte eksekvérbar og følge babyAI-arkitekturkonventioner.

## Workflow

### 1. Forstå scope
- Hvad skal bygges? (user story / feature / bugfix)
- Hvilke eksisterende services er involveret?
- Er der nye services nødvendige?

### 2. Identificer komponenter (via RepoGraph)
- Tjek eksisterende patterns: `skill_mds()`, `artifact_writers()`, `consumers_of_topic()`
- Identificer hvilke services der skal ændres
- Find relevante Kafka-topics

### 3. Nedbryd i faser
Strukturér opgaven i logiske faser:
- **Fase 1**: Fundament (datamodeller, Kafka-schemas, DB-migrations)
- **Fase 2**: Core logic (services, adapters, policies)
- **Fase 3**: Integration (wiring, Kafka-topics, ECB-gating)
- **Fase 4**: Test + verify (unit, integration, smoke)

### 4. Estimer
Per task: S (1-2t) / M (halv dag) / L (1 dag) / XL (2+ dage)

### 5. Identificer risici
Breaking changes, manglende schemas, performance-risici.

## Output-format

```
## Implementation Plan: {feature-titel}

### Oversigt
{2-3 linjers beskrivelse af hvad der bygges og hvorfor}

### Berørte services
- {service-navn}: {hvad ændres}

### Faser

#### Fase 1: {navn} — Estimat: {N} dage
- [ ] {task} ({S|M|L|XL}) — {acceptance criteria}
- [ ] {task}

#### Fase 2: {navn} — Estimat: {N} dage
- [ ] {task}

### Kafka-topics
| Topic | Producer | Consumer | Schema |
|-------|----------|----------|--------|
| {topic} | {service} | {service} | {schema-ref} |

### Afhængigheder
- {task A} skal være done før {task B}

### Risici
🔴 {kritisk}: {beskrivelse + mitigation}
🟠 {alvorlig}: {beskrivelse}

### Samlet estimat
{N} dage / {range} dage (optimistisk–pessimistisk)
```
