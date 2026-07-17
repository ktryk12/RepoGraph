---
skill_id: review
name: review
version: 1.0.0
description: >
  Udfør en struktureret code review. Triggers: "review", "gennemgå kode",
  "tjek kode", "code review", "hvad er galt med", "kan du se fejl i".
  Producerer findings med sværhedsgrad, anbefalinger og auto-fix forslag.
domains: [code, quality, security]
dimensions: [review, analysis, findings]
triggers:
  - review
  - gennemgå kode
  - tjek kode
  - code review
  - hvad er galt med
expert_routing:
  model: code-codestral
  danish_version: true
  temperature: 0.2
  max_tokens: 2000
babyai_conventions:
  respect:
    - artifact-writer er eneste skrive-vej
    - policies valideres af policy-validator
  flag:
    - direkte database-skrivninger udenom artifact-writer
    - Kafka-producers uden schema-validering
telemetry:
  emit_events:
    - skill.review.started
    - skill.review.completed
---

## Formål

Analyser kode og identificer: fejl, sikkerhedsrisici, arkitektur-problemer,
manglende tests, policy-overtrædelser og performance-problemer.

## Workflow

1. Læs koden i kontekst (brug RepoGraph til at forstå afhængigheder)
2. Identificer findings per kategori:
   - 🔴 KRITISK: sikkerhed, datalæk, crash-risiko
   - 🟠 ALVORLIG: logikfejl, manglende fejlhåndtering
   - 🟡 ADVARSEL: code smell, duplikering, manglende tests
   - 🔵 INFO: style, navngivning, dokumentation
3. For hvert finding: giv konkret fix-forslag (max 5 linjer kode)
4. Tjek babyAI-konventioner:
   - Skrives via artifact-writer? (flag hvis ikke)
   - ECB-gating på porte/adapters?
   - Kafka-schema-validering til stede?

## Output-format

```
## Code Review: {fil/modul}

### Findings ({antal} total)

**🔴 KRITISK — {titel}**
Linje {N}: {beskrivelse}
FIX: ```{sprog}
{fix-kode}
```

### Resumé
- {antal} kritiske, {antal} alvorlige, {antal} advarsler
- Anbefaling: GODKEND | GODKEND MED RETTELSER | AFVIS
```

## babyAI-specifikke tjek

- [ ] Ingen direkte SQLite-skrivninger (brug artifact-writer)
- [ ] Kafka-events har schema-validering
- [ ] Ingen API-nøgler i kode
- [ ] Alle nye services har /health endpoint
