---
skill_id: plan-ceo-review
name: plan-ceo-review
version: 1.0.0
description: >
  CEO/strategisk review af en implementeringsplan. Triggers: "ceo review",
  "plan-ceo-review", "er planen forretningsmæssigt holdbar", "strategisk vurdering",
  "business case", "ROI på planen". Vurderer forretningsværdi, prioritering og ressourcer.
domains: [planning, strategy, business]
dimensions: [ceo-review, roi, prioritization]
triggers:
  - ceo review
  - plan-ceo-review
  - er planen forretningsmæssigt holdbar
  - strategisk vurdering
  - business case
expert_routing:
  model: danish
  danish_version: true
  temperature: 0.4
  max_tokens: 1500
babyai_conventions:
  respect:
    - artifact-writer er eneste skrive-vej
    - prioritering baseres på revenue-impact
  flag:
    - planer uden klart business outcome
    - features der ikke understøtter kerneprodukt
telemetry:
  emit_events:
    - skill.plan-ceo-review.started
    - skill.plan-ceo-review.completed
---

## Formål

Vurdér en implementeringsplan fra et forretningsmæssigt og strategisk perspektiv:
ROI, prioritering, ressourceforbrug og alignment med produktvision.

## Tjekliste

### Forretningsværdi
- [ ] Er der et klart business outcome? (revenue, retention, cost reduction)
- [ ] Er ROI estimeret — selv groft?
- [ ] Understøtter planen nuværende produktstrategi?
- [ ] Er der alternative tilgange med bedre ROI?

### Prioritering
- [ ] Er dette det vigtigste at bygge nu?
- [ ] Hvad er opportunity cost ved at gøre dette i stedet for X?
- [ ] Er tidsestimater realistiske set fra ressource-perspektiv?

### Risici (forretning)
- [ ] Afhænger planen af externe parter / tredjepartsintegrationer?
- [ ] Er der compliance/legal overvejelser?
- [ ] Er scope creep sandsynligt?

### Go-to-market
- [ ] Er der en launch-plan?
- [ ] Hvem er de første brugere?
- [ ] Hvordan måler vi succes? (metrikker)

## Output-format

```
## CEO Review: {plan-titel}

### Vurdering: GODKEND | GODKEND MED ÆNDRINGER | AFVIS | UDSKY

### Forretningsbegrundelse
{Kort vurdering af forretningsmæssig relevans og timing}

### ROI-vurdering
- Estimeret værdi: {HIGH|MEDIUM|LOW}
- Tidshorisont: {immediate|3m|6m|12m+}
- Alternativomkostning: {hvad vi ikke laver}

### Strategiske risici ({antal})
🔴 {kritisk}: {beskrivelse}
🟠 {alvorlig}: {beskrivelse}

### Anbefalinger
1. {konkret anbefaling}

### Succes-metrikker
- {målbar KPI} inden {dato}
```
