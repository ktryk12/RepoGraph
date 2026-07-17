# BabyAI Roadmap

## Phase 1: Crypto Intelligence Layer (CURRENT) ✅ 80%

**Goal:** Real-time crypto market intelligence flowing through Kafka, with human-gated agent bootstrapping.

### Completed
- [x] CryptoIntelAgent (60s polling, Kafka publish to signal.crypto.*)
- [x] KafkaProvisioner (idempotent topic/consumer management)
- [x] AgentBootstrapUseCase (atomic bootstrap with rollback, audit log)
- [x] GapDetectorAgent (gap detection, confidence scoring, log proposals)
- [x] babyai.cli approve-gap / reject-gap
- [x] SupervisorAgent routing for CRYPTO_INTEL_SIGNAL + INFRA_GAP_DETECTED

### Remaining
- [ ] OpenBB MCP integration (financial data layer)
- [ ] Firecrawl integration (web intelligence)
- [ ] FinRobot adapter (multi-agent finance analysis)

**Exit criteria:** All crypto data sources connected; gap detection running in background; at least one approved agent bootstrap tested end-to-end.

---

## Phase 2: Trading Execution

**Goal:** Supervised trade execution with policy enforcement.

- [ ] TradingAgent policy (asset overlap prevention, position sizing)
- [ ] BinanceTraderAgent (spot + futures)
- [ ] eToroTraderAgent (stocks + crypto)
- [ ] LoRA training dataset pipeline from challenge run logs

**Exit criteria:** A paper trade can be proposed by CryptoIntelAgent, approved via CLI, and executed by BinanceTraderAgent.

---

## Phase 3: Intelligence Network

**Goal:** Macro + political + whale signal coverage for correlation-based decisions.

- [ ] PoliticalIntelAgent (QuiverQuant + Capitol Trades)
- [ ] WhaleWatcherAgent (SEC 13F + Whale Alert)
- [ ] MacroNewsAgent (Fed/ECB/Nationalbanken RSS)
- [ ] CorrelationAgent (cross-signal pattern matching)

**Exit criteria:** CorrelationAgent produces regime-classified signals consumed by TradingAgent policy layer.

---

## Architecture Principles (preserved across all phases)

- **L7 boundary**: `requires_action = False` always; no automatic agent creation
- **Inline topic constants**: agents never `from bus.topics import ...`
- **Never-raises**: use-cases return result objects, never raise
- **JSON lines**: all audit/gap logs are append-only JSON lines
- **Confidence gates**: emit at ≥ 0.50, Kafka publish at ≥ 0.70
