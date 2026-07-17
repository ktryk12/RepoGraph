---
skill_id: learn
name: learn
version: 1.0.0
description: >
  Lær fra en observation, fejl eller erfaring og persistér til memory-plane.
  Triggers: "lær dette", "husk at", "gem denne erfaring", "learn",
  "tilføj til hukommelse", "næste gang skal vi".
domains: [learning, memory, improvement]
dimensions: [memory-plane, knowledge, persistence]
triggers:
  - lær dette
  - husk at
  - gem denne erfaring
  - learn
  - tilføj til hukommelse
  - næste gang skal vi
expert_routing:
  model: danish
  danish_version: true
  temperature: 0.3
  max_tokens: 800
babyai_conventions:
  respect:
    - persistér via memory-plane API — ikke direkte filskrivning
    - artifact-writer er eneste skrive-vej for artifacts
  flag:
    - direkte filskrivninger til memory-mappen udenom memory-plane
telemetry:
  emit_events:
    - skill.learn.started
    - skill.learn.completed
---

## Formål

Strukturér og persistér en læring til memory-plane så fremtidige
skill-executions kan drage nytte af den.

## Workflow

1. **Klassificér lærings-typen:**
   - `feedback` — korrektioner og præferencer (vigtigst)
   - `project` — fakta om igangværende arbejde
   - `user` — information om brugeren
   - `reference` — pointer til ekstern ressource

2. **Strukturér lærings-indholdet:**
   - Hvad er reglen/fakta? (Lead-sætning)
   - Hvorfor? (Why-linje)
   - Hvornår anvendes det? (How-to-apply)

3. **Persistér via memory-plane:**
   POST til memory-plane API med type + indhold

4. **Bekræft** at lærings-ID er returneret

## Memory-typer og hvornår

| Type | Brug når |
|------|----------|
| `feedback` | Bruger korrigerer adfærd eller bekræfter en uventet tilgang |
| `project` | Du lærer om deadlines, stakeholders, igangværende arbejde |
| `user` | Du lærer om brugerens rolle, ekspertise, præferencer |
| `reference` | Du lærer om ekstern ressource (Linear, Grafana, Slack) |

## Output-format

```
## Læring gemt ✅

**Type:** {feedback|project|user|reference}
**Titel:** {kort titel}
**Indhold:** {struktureret indhold}
**Memory-ID:** {id fra memory-plane}

Denne læring vil blive anvendt i fremtidige konversationer.
```

## Hvad der IKKE gemmes

- Kode-mønstre (læses fra kodebasen)
- Git-historik (bruges git log)
- Midlertidige opgave-detaljer
