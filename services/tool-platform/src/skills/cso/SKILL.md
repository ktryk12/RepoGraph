---
skill_id: cso
name: cso
version: 1.0.0
description: >
  Security Officer review: OWASP Top 10 + STRIDE threat modelling.
  Triggers: "sikkerhedsreview", "security review", "cso", "trusler",
  "STRIDE", "OWASP", "hvad kan gå galt", "threat model".
domains: [security, compliance, risk]
dimensions: [owasp, stride, threat-model]
triggers:
  - sikkerhedsreview
  - security review
  - cso
  - trusler
  - threat model
  - OWASP
  - STRIDE
expert_routing:
  model: general
  danish_version: true
  temperature: 0.2
  max_tokens: 2500
babyai_conventions:
  respect:
    - artifact-writer er eneste skrive-vej
    - ECB-gating for ports/adapters
    - policies valideres af policy-validator
  flag:
    - API-nøgler i kode eller logs
    - Kafka-producers uden schema-validering
    - Direkte netværkskald uden rate-limiting
telemetry:
  emit_events:
    - skill.cso.started
    - skill.cso.completed
---

## Formål

Gennemfør security review mod OWASP Top 10 og STRIDE threat model.
Identificer trusler, angrebsvektorer og mitigations.

## OWASP Top 10 tjekliste

- [ ] A01 Broken Access Control — hvem kan kalde hvad?
- [ ] A02 Cryptographic Failures — krypteres data at-rest og in-transit?
- [ ] A03 Injection — SQL/Kafka/command injection mulig?
- [ ] A04 Insecure Design — er threat model lavet?
- [ ] A05 Security Misconfiguration — default passwords/tokens?
- [ ] A06 Vulnerable Components — outdated deps?
- [ ] A07 Auth Failures — sessions, tokens, API-keys håndteret korrekt?
- [ ] A08 Software Integrity — dependency integrity verificeret?
- [ ] A09 Logging Failures — logges sikkerhedshændelser?
- [ ] A10 SSRF — kan service foretage uautoriserede requests?

## STRIDE Threat Model

For hvert system-element:

| Element | S | T | R | I | D | E |
|---------|---|---|---|---|---|---|
| {komponent} | ? | ? | ? | ? | ? | ? |

(S=Spoofing, T=Tampering, R=Repudiation, I=InfoDisclosure, D=DoS, E=Elevation)

## babyAI-specifikke sikkerhedstjek

- [ ] API-nøgler i Vault — aldrig i kode eller logs
- [ ] Kafka-events schema-valideret (ingen injection via payload)
- [ ] artifact-writer er eneste skrive-vej (ingen direkte fil-skrivning fra agents)
- [ ] ECB-gating aktiv på alle execution-endpoints
- [ ] Kill-switch tilgængeligt på broker-gateway

## Output-format

```
## Security Review: {system/modul}

### Kritiske fund ({antal})
🔴 {OWASP/STRIDE reference}: {beskrivelse}
   Risiko: {HIGH|MEDIUM|LOW}
   Mitigation: {konkret fix}

### Samlet risikovurdering
{GRØN|GUL|RØD} — {begrundelse}

### Anbefalinger (prioriteret)
1. {kritisk — fix inden deploy}
2. {vigtigt — fix inden næste sprint}
```
