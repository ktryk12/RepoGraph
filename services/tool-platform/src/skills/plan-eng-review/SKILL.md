---
skill_id: plan-eng-review
name: plan-eng-review
version: 1.0.0
description: >
  Engineering review af en implementeringsplan. Triggers: "review planen",
  "plan-eng-review", "er planen teknisk holdbar", "gennemgå implementation plan",
  "check planen". Vurderer teknisk feasibility, afhængigheder og risici.
domains: [code, planning, architecture]
dimensions: [plan-review, feasibility, risk]
triggers:
  - review planen
  - plan-eng-review
  - er planen teknisk holdbar
  - gennemgå implementation plan
expert_routing:
  model: code-codestral
  danish_version: true
  temperature: 0.2
  max_tokens: 2000
babyai_conventions:
  respect:
    - artifact-writer er eneste skrive-vej
    - ECB-gating for ports/adapters
    - policies valideres af policy-validator
  flag:
    - planer der omgår artifact-writer
    - manglende Kafka-schema-definitioner
telemetry:
  emit_events:
    - skill.plan-eng-review.started
    - skill.plan-eng-review.completed
---

## Formål

Vurdér en implementeringsplan fra et teknisk perspektiv:
feasibility, afhængigheder, risici og mangler.

## Tjekliste

### Arkitektur
- [ ] Følger planen eksisterende mønstre i kodebasen?
- [ ] Er nye services nødvendige, eller kan eksisterende genbruges?
- [ ] Er afhængigheder (Kafka-topics, DB-schemas, ports) defineret?

### Risici
- [ ] Breaking changes til eksisterende services?
- [ ] Data-migrations nødvendige?
- [ ] Performance-risici (N+1, manglende cache, sync I/O)?

### babyAI-konventioner
- [ ] Alle skrivninger via artifact-writer?
- [ ] Kafka-events schema-valideret?
- [ ] Nye ports ECB-gated?
- [ ] /health endpoint på nye services?

### Test-strategi
- [ ] Unit tests planlagt?
- [ ] Integration-tests mod rigtige Kafka-topics?
- [ ] Smoke-test til verify-suite?

## Output-format

```
## Engineering Review: {plan-titel}

### Vurdering: GODKEND | GODKEND MED ÆNDRINGER | AFVIS

### Tekniske risici ({antal})
🔴 {kritisk risiko}: {beskrivelse + mitigation}
🟠 {alvorlig risiko}: {beskrivelse}

### Mangler i planen
- {manglende element}

### Anbefalede ændringer
1. {konkret ændring}

### Estimat-vurdering
{Er tidsestimater realistiske? Kommentarer.}
```
