---
skill_id: office-hours
name: office-hours
version: 1.0.0
description: >
  Åben teknisk Q&A session om babyAI-kodebasen og arkitektur.
  Triggers: "office hours", "spørgsmål om koden", "forklar arkitekturen",
  "hvordan virker X", "hvad er forskellen på", "kan du forklare".
  Bruger RepoGraph til at give præcise, evidensbaserede svar.
domains: [documentation, architecture, education]
dimensions: [qa, explanation, codebase-navigation]
triggers:
  - office hours
  - spørgsmål om koden
  - forklar arkitekturen
  - hvordan virker
  - hvad er forskellen på
  - kan du forklare
expert_routing:
  model: danish
  danish_version: true
  temperature: 0.5
  max_tokens: 2000
babyai_conventions:
  respect:
    - svar baseres på faktisk kode via RepoGraph — ikke antagelser
    - henvis altid til konkrete filer og linjenumre
  flag:
    - svar der ikke er forankret i faktisk kode
telemetry:
  emit_events:
    - skill.office-hours.started
    - skill.office-hours.completed
---

## Formål

Besvar tekniske spørgsmål om babyAI-kodebasen med præcision og evidens.
Brug RepoGraph til at finde den faktiske kode bag svaret.

## Workflow

### 1. Forstå spørgsmålet
- Hvad spørges der om? (arkitektur / specifik service / pattern / convention)
- Hvilket abstraktionsniveau forventes? (overordnet / detaljeret / kode-niveau)

### 2. Find evidens (RepoGraph)
Brug relevante RepoGraph-kald:
- `find_symbol(name)` — find specifik klasse/funktion
- `consumers_of_topic(topic)` — hvem lytter på et Kafka-topic?
- `blast_radius(symbol)` — hvad påvirkes hvis X ændres?
- `skill_mds()` — hvad er de tilgængelige skills?
- `reuse_report()` — hvilke komponenter er genbrugelige?

### 3. Formulér svar
- Start med en 1-sætnings konklusion
- Underbyg med kode-referencer (fil + linje)
- Giv eksempel hvis relevant
- Nævn edge cases eller gotchas

### 4. Foreslå relaterede emner
Hvad er naturligt at spørge om bagefter?

## Output-format

```
## {Spørgsmål (omformuleret som overskrift)}

### Kort svar
{1-2 sætninger}

### Detaljeret forklaring
{teknisk forklaring med kode-referencer}

**Reference:** `{fil}:{linje}` — {beskrivelse}

### Eksempel
```{sprog}
{kode-eksempel}
```

### Relaterede emner
- {naturligt næste spørgsmål}
- {relateret koncept}
```

## Hvad office-hours IKKE gør

- Laver kode-ændringer (brug `/review` eller bed om en implementation)
- Besvarer spørgsmål om externe systemer uden for kodebasen
- Gætter — hvis svaret ikke kan forankres i kode, siges det eksplicit
