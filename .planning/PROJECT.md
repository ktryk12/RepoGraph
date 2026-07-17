# BabyAI

## What This Is

Locally-run multi-agent AI orchestration platform with a Kafka message bus, Redis, and a policy-first architecture. Crypto and finance intelligence agents observe markets, detect coverage gaps, and propose agent bootstraps for human approval. All agent creation requires explicit human sign-off (L7 boundary enforced in code).

## Core Value

A human-supervised, locally-hosted intelligence network that turns raw market signals into structured decisions — without any automatic agent creation or external data exfiltration.

## Requirements

### Validated

- [x] CryptoIntelAgent — 60s polling, Kafka publish to signal.crypto.*
- [x] KafkaProvisioner — idempotent topic + consumer group management via AdminClient
- [x] AgentBootstrapUseCase — atomic bootstrap with rollback, audit log, DLQ on failure
- [x] GapDetectorAgent — detects uncovered Kafka topics, logs proposals for human review
- [x] babyai.cli — `approve-gap` / `reject-gap` CLI with JSON-lines status rewrite
- [x] SupervisorAgent routing — CRYPTO_INTEL_SIGNAL + INFRA_GAP_DETECTED handlers

### Active

- [ ] OpenBB MCP integration (financial data layer)
- [ ] Firecrawl integration (web intelligence)
- [ ] FinRobot adapter (multi-agent finance analysis)
- [ ] TradingAgent policy (asset overlap prevention)
- [ ] BinanceTraderAgent (spot + futures)
- [ ] eToroTraderAgent (stocks + crypto)
- [ ] PoliticalIntelAgent (QuiverQuant + Capitol Trades)
- [ ] WhaleWatcherAgent (SEC 13F + Whale Alert)
- [ ] MacroNewsAgent (Fed/ECB/Nationalbanken RSS)
- [ ] CorrelationAgent (cross-signal pattern matching)

### Out of Scope

- Automatic agent creation without human approval — L7 boundary is a hard constraint; `requires_action` is always `False`
- Cloud deployment — runs locally only; no external data exfiltration
- GPT/non-Claude models — Claude Code + local LoRA models only

## Context

- **Tech stack**: Python 3.13, Kafka (confluent-kafka 2.13.0), Redis, Docker Compose, FastAPI
- **Message bus**: `bus/` package; agents use inline topic string constants to avoid import chain issues (`bus/__init__` → `bus.message_bus` → `babyai_shared`)
- **Dual protocol files**: `agents/protocol.py` and `shared/babyai_shared/bus/protocol.py` must stay in sync for MessageType additions
- **Test pattern**: `_CapturingPublisher` / `_CapPublisher` stubs; assertions on temp files inside `with tempfile.TemporaryDirectory()` block
- **Confidence gates**: signals below 0.50 are noise-filtered; Kafka publish requires ≥ 0.70
- **Existing test suites**: 19/19 passing across kafka_provisioner, gap_detector, crypto_intel_agent smoke tests

## Constraints

- **Security**: L7 boundary — no automatic agent creation; `requires_action = False` enforced at every layer
- **Dependencies**: confluent_kafka only (no kafka-python); redis optional with in-process dict fallback
- **Imports**: Agents must be independently importable without `babyai_shared` installed; use lazy or inline imports for bus/shared packages
- **Platform**: Windows 11 + WSL2 for Docker; bash shell via Git Bash

## Key Decisions

- **Inline topic constants** in agent files rather than `from bus.topics import ...` — prevents import chain failures in tests
- **Never-raises pattern** for `AgentBootstrapUseCase.execute()` and `GapDetectorAgent._emit()` — always returns result/None
- **JSON lines** for all audit logs (gap_detector.log, agent_bootstrap.log) — append-only, human-readable, easy to rewrite in-place
- **CLI gap approval** writes status in-place by rewriting the entire log file atomically
