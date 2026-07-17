---
skill_id: retro
name: retro
version: 1.0.0
description: >
  Sprint-retrospektiv baseret på artifact-writer historik og telemetri.
  Triggers: "retro", "retrospektiv", "hvad lærte vi", "sprint review",
  "hvad gik godt", "hvad skal vi forbedre".
  Kører automatisk nightly (cron) og ved sprint-afslutning.
domains: [process, learning, quality]
dimensions: [retrospective, improvement, metrics]
triggers:
  - retro
  - retrospektiv
  - hvad lærte vi
  - sprint review
  - hvad gik godt
expert_routing:
  model: danish
  danish_version: true
  temperature: 0.4
  max_tokens: 2000
babyai_conventions:
  respect:
    - artifact-writer er eneste skrive-vej
    - læs fra execution-audit ikke direkte fra SQLite
  flag:
    - direkte database-queries udenom artifact-writer
telemetry:
  emit_events:
    - skill.retro.started
    - skill.retro.completed
---

## Formål

Gennemfør sprint-retrospektiv baseret på objektive data fra:
- `skill.execution.completed` events (hvad kørte, med hvilke resultater?)
- `execution-audit` daglige P&L-rapporter
- `review` findings fra perioden
- `cso` sikkerhedsfund

## Workflow

### 1. Hent data (artifact-reader)
- Antal skill-executions i perioden
- Acceptance rate (user_accepted = true / total)
- Top findings fra `/review`-executions
- Kritiske sikkerhedsfund fra `/cso`

### 2. Analyse: Hvad gik godt?
Identificer mønstre i vellykkede executions.
Hvilke skills har høj acceptance rate?

### 3. Analyse: Hvad skal forbedres?
- Lav acceptance rate → prompt-forbedring?
- Mange policy-violations → missing guardrails?
- Høj latency → model-routing problem?

### 4. Action items (max 5)
Konkrete, målbare forbedringer til næste sprint.

## Output-format

```
## Retrospektiv: {periode}

### Nøgletal
- Skill executions: {N}
- Acceptance rate: {X}%
- Policy violations: {N}
- Gennemsnits-latency: {N}ms

### ✅ Hvad gik godt
{3 positive observationer med evidens}

### 🔧 Hvad skal forbedres
{3 forbedringspunkter med konkrete action items}

### 📋 Action items til næste sprint
1. [ ] {konkret opgave} — ansvarlig: {skill/service}
2. [ ] {konkret opgave}
```

## Nightly cron

Kører automatisk kl. 03:00 UTC.
Gemmer artifact til `artifacts/retro/YYYY-MM-DD.md` via artifact-writer.
