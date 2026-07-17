---
skill_id: document-release
name: document-release
version: 1.0.0
description: >
  Generer release notes og changelog fra git-historik og artifact-writer output.
  Triggers: "document release", "lav release notes", "skriv changelog",
  "hvad er nyt i denne version", "release dokumentation".
  Producerer CHANGELOG.md og release artifact via artifact-writer.
domains: [documentation, release, communication]
dimensions: [changelog, release-notes, versioning]
triggers:
  - document release
  - lav release notes
  - skriv changelog
  - hvad er nyt i denne version
  - release dokumentation
expert_routing:
  model: danish
  danish_version: true
  temperature: 0.3
  max_tokens: 2000
babyai_conventions:
  respect:
    - artifact-writer er eneste skrive-vej
    - semver bruges til versionering (MAJOR.MINOR.PATCH)
    - git log er kilde til fakta — ikke antagelser
  flag:
    - release notes uden version-tag
    - breaking changes der ikke er markeret tydeligt
telemetry:
  emit_events:
    - skill.document-release.started
    - skill.document-release.completed
---

## Formål

Producér strukturerede release notes og CHANGELOG-opdatering baseret på
git-historik siden sidste release-tag.

## Workflow

### 1. Find scope
- Hvad er den aktuelle version? (`git describe --tags`)
- Hvad er forrige release? (`git log --oneline {prev-tag}..HEAD`)
- Er der breaking changes? (søg i commits efter "BREAKING", "breaking change")

### 2. Kategorisér commits
Gruppér commits i:
- **Ny funktionalitet** (feat:, add, new)
- **Fejlrettelser** (fix:, bugfix, hotfix)
- **Performance** (perf:, optimize)
- **Refaktorering** (refactor:, cleanup)
- **Breaking changes** (BREAKING CHANGE, breaking:)
- **Infrastruktur** (ci:, docker, deploy)

### 3. Skriv release notes
- Bruger-venligt sprog (ikke commit-hash-sprog)
- Breaking changes først og tydeligt
- Link til relevante PRs/issues hvis muligt

### 4. Gem via artifact-writer
- CHANGELOG.md opdateres (prepend ny sektion)
- Release artifact: `artifacts/releases/{version}.md`

## Output-format

```
## Release Notes: v{MAJOR.MINOR.PATCH}

**Dato:** {YYYY-MM-DD}
**Type:** {MAJOR|MINOR|PATCH}

### ⚠️ Breaking Changes
- {breaking change beskrivelse + migration-guide}

### ✨ Ny funktionalitet
- {feature}: {beskrivelse}

### 🐛 Fejlrettelser
- {fix}: {beskrivelse}

### ⚡ Performance
- {forbedring}: {beskrivelse}

### 🔧 Infrastruktur
- {ændring}: {beskrivelse}

### Opgradering
```bash
{opgraderingsinstruktioner}
```

**Fuld diff:** `git log {prev-tag}..{new-tag} --oneline`
```

## CHANGELOG.md format

Følger [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) standard:
- Unreleased sektion øverst
- Nyeste releases først
- Dato på alle releases
