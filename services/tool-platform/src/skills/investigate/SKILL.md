---
skill_id: investigate
name: investigate
version: 1.0.0
description: >
  Undersøg et bug, en fejl eller et ukendt system-fænomen via Iron Law:
  hypotese → bevis → konklusion. Triggers: "undersøg", "hvorfor fejler",
  "debug", "investigate", "hvad sker der med", "find årsagen til".
domains: [debugging, analysis, operations]
dimensions: [investigation, root-cause, hypothesis]
triggers:
  - undersøg
  - hvorfor fejler
  - debug
  - investigate
  - find årsagen
  - hvad sker der med
expert_routing:
  model: general
  danish_version: true
  temperature: 0.3
  max_tokens: 2000
babyai_conventions:
  respect:
    - artifact-writer er eneste skrive-vej
    - ECB-gating for ports/adapters
  flag:
    - direkte database-skrivninger udenom artifact-writer
telemetry:
  emit_events:
    - skill.investigate.started
    - skill.investigate.completed
---

## Formål

Systematisk root-cause analyse efter Iron Law-mønsteret:
**Observér → Hypoteser → Bevis/Afkræft → Konklusion → Fix**

## Workflow (Iron Law)

### 1. Observér
- Hvad er det præcise symptomet? (fejlbesked, adfærd, metric)
- Hvornår startede det? (timestamp, deploy, config-ændring)
- Hvad er scope? (alle brugere / én service / bestemt input)

### 2. Hypoteser (max 3)
Generér 3 mulige årsager, rangeret efter sandsynlighed.
For hver: hvad ville bevise den?

### 3. Bevis eller afkræft
Brug RepoGraph til at finde relevant kode.
Tjek:
- Kafka-topics: er producer/consumer korrekt koblet?
- Artifact-writer: skrives resultater korrekt?
- Policy-validator: er der afvisninger i loggen?
- Redis-cache: TTL-problemer?

### 4. Konklusion
Præsenter root cause med evidens.

### 5. Fix-forslag
Konkret code-snippet eller config-ændring.

## Output-format

```
## Investigation: {problem-titel}

**Observeret symptom:** {beskrivelse}

### Hypoteser
1. [SANDSYNLIG] {hypotese} — bevis: {hvad vi leder efter}
2. [MULIG] {hypotese}
3. [USANDSYNLIG] {hypotese}

### Analyse
{findings fra kode/logs/repograph}

### Root Cause
{konklusion med evidens}

### Fix
```{sprog}
{fix-kode}
```
**Verificering:** {hvordan vi ved det virker}
```
