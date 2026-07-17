---
skill_id: skill-id-here
name: skill-id-here
version: 1.0.0
description: >
  Kort beskrivelse af hvad skillen gør. Triggers: "trigger 1",
  "trigger 2", "trigger 3". Skriv 1-2 sætninger om hvad der sker.
domains: [domain1, domain2]
dimensions: [dim1, dim2]
triggers:
  - trigger 1
  - trigger 2
  - trigger 3
expert_routing:
  model: general                  # code-codestral | general | danish | vision
  danish_version: true
  temperature: 0.3                # 0.1=deterministisk, 0.5=kreativ
  max_tokens: 1500
babyai_conventions:
  respect:
    - artifact-writer er eneste skrive-vej
    - ECB-gating for ports/adapters
    - policies valideres af policy-validator
  flag:
    - {hvad der skal markeres som problematisk}
telemetry:
  emit_events:
    - skill.{skill-id}.started
    - skill.{skill-id}.completed
---

## Formål

{Beskriv formålet med skillen i 2-4 sætninger.
Hvad er den designet til at gøre?
Hvornår bruges den?}

## Workflow

### 1. {Første trin}
{Beskrivelse af første trin i skillen.}

### 2. {Andet trin}
{Beskrivelse. Brug RepoGraph-kald her hvis relevant:
- `find_symbol(name)` — find klasse/funktion
- `consumers_of_topic(topic)` — Kafka-lyttere
- `blast_radius(symbol)` — impact-analyse}

### 3. {Tredje trin}
{Beskrivelse.}

### 4. Output
{Hvad producerer skillen? Artifact? Tekst? Kode?}

## Output-format

```
## {Skill-overskrift}: {input-titel}

### {Sektion 1}
{indhold}

### {Sektion 2}
{indhold}

### Anbefalinger
1. {konkret anbefaling}
2. {konkret anbefaling}
```

## Hvad denne skill IKKE gør

- {grænse 1}
- {grænse 2}

---

<!-- KOPIER DENNE TEMPLATE TIL skills/{ny-skill}/SKILL.md og udfyld felterne -->
<!-- Kør `skill_runtime validate` efter oprettelse for at verificere schema -->
