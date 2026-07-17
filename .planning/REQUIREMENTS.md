# BabyAI Requirements

## Table Stakes (must work for the project to be viable)

- [ ] Kafka topics provisioned idempotently before any agent starts
- [ ] All agent creation gated behind human approval (`approve-gap` CLI)
- [ ] Audit trail for every bootstrap attempt (agent_bootstrap.log)
- [ ] Agents independently importable without full `babyai_shared` install
- [ ] Smoke tests run without a live Kafka/Redis broker

## Milestone 1: Crypto Intelligence Layer

### Done
- [x] CryptoIntelAgent polls CoinGecko + Binance every 60s
- [x] Whale Alert fallback when API key absent
- [x] Redis dedup with in-process dict fallback
- [x] KafkaProvisioner: `ensure_topic`, `ensure_topics`, `ensure_consumer_group`
- [x] AgentBootstrapUseCase: 4-step bootstrap, rollback to DLQ on failure
- [x] GapDetectorAgent: 300s scan cycle, confidence scoring, _MIN_CONFIDENCE filter
- [x] _LogWriter: JSON lines + Markdown proposals
- [x] babyai.cli: `approve-gap` / `reject-gap` with in-place log rewrite

### Remaining
- [ ] OpenBB MCP integration — structured financial data (earnings, macro, options flow)
- [ ] Firecrawl MCP integration — web scraping for news intelligence
- [ ] FinRobot adapter — multi-agent finance analysis pipeline

## Milestone 2: Trading Execution

- [ ] TradingAgent policy — asset overlap prevention, position sizing rules
- [ ] BinanceTraderAgent — spot and futures order execution
- [ ] eToroTraderAgent — stocks + crypto via eToro MCP
- [ ] LoRA training dataset pipeline from challenge run logs

## Milestone 3: Intelligence Network

- [ ] PoliticalIntelAgent — QuiverQuant + Capitol Trades congressional trading data
- [ ] WhaleWatcherAgent — SEC 13F filings + Whale Alert large transactions
- [ ] MacroNewsAgent — Fed/ECB/Nationalbanken RSS → structured macro signals
- [ ] CorrelationAgent — cross-signal pattern matching, regime detection

## Non-Requirements (explicit exclusions)

- Automatic agent creation — `requires_action` is always `False`; humans approve via CLI
- Cloud/SaaS deployment — local only
- External API keys stored in code — env vars only
- Non-Claude inference models
