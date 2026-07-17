"""
bus/topics.py — Single canonical Kafka topic registry.

Two tiers coexist in this file and must never be confused:

Tier 1 — Legacy text topics
    Production-critical.  These are the topics that currently
    power every agent, consumer, and orchestrator in the system.
    They are reproduced here verbatim from config/kafka_config.yaml.
    Do NOT rename, remove, or repurpose any Tier 1 constant.
    The config file remains the deployment-time override mechanism;
    these constants are the Python source of truth for code that
    needs to reference topic names without loading YAML at import time.

Tier 2 — Latent layer topics (Sprint A1+)
    Additive only — they never replace or shadow Tier 1 topics.
    Separated into two tracks:

    Spor 1 — Runtime track
        Lives on the production path alongside Tier 1 topics.
        Producers and consumers arrive in Sprint A1 Part 2.

    Spor 2 — Learning track
        Experimental; never production-critical.
        Used by the JEPA predictor (A2) and self-correction loop (A5).
        These topics may be absent in production deployments without
        causing system failure.

Migration note
--------------
All Tier 1 topic strings are currently hardcoded as bare strings
in many agents and workers (e.g. orchestrator_worker.py uses a
_topic_name() helper with inline defaults; planner, request_gate,
truthpack_conversation each have their own inline defaults).
Sprint A1 Part 2 should migrate those callers to these constants.
Until then, the topic strings themselves are unchanged and the
existing runtime is unaffected.
"""

# ---------------------------------------------------------------------------
# Tier 1 — Legacy text topics
# Production-critical. Matches config/kafka_config.yaml exactly.
# ---------------------------------------------------------------------------

# Decision flow
DECISION_INTENT = "decision.intent"
DECISION_TRUTHPACK_QUESTIONS = "decision.truthpack.questions"
DECISION_TRUTHPACK_ANSWERS = "decision.truthpack.answers"
DECISION_TRUTHPACK_READY = "decision.truthpack.ready"
DECISION_REQUESTED = "decision.requested"
DECISION_LIFECYCLE = "decision.lifecycle"
DECISION_APPROVAL = "decision.approval"
DECISION_LIFECYCLE_DLQ = "decision.lifecycle.dlq"

# Policy flow
POLICY_DISCOVERY_COMPLETE = "policy.discovery.complete"
POLICY_DRAFT_READY = "policy.draft.ready"
POLICY_BOOTSTRAP_DLQ = "policy.bootstrap.dlq"
POLICY_APPROVED = "policy.approved"
POLICY_REJECTED = "policy.rejected"

# Evaluation, tooling, artifacts
EVAL_RESULTS = "eval.results"
TOOL_EVENTS = "tool.events"
ARTIFACT_EVENTS = "artifact.events"

# All Tier 1 topics as a frozenset — use for validation and enumeration.
TIER_1_TOPICS: frozenset[str] = frozenset(
    {
        DECISION_INTENT,
        DECISION_TRUTHPACK_QUESTIONS,
        DECISION_TRUTHPACK_ANSWERS,
        DECISION_TRUTHPACK_READY,
        DECISION_REQUESTED,
        DECISION_LIFECYCLE,
        DECISION_APPROVAL,
        DECISION_LIFECYCLE_DLQ,
        POLICY_DISCOVERY_COMPLETE,
        POLICY_DRAFT_READY,
        POLICY_BOOTSTRAP_DLQ,
        POLICY_APPROVED,
        POLICY_REJECTED,
        EVAL_RESULTS,
        TOOL_EVENTS,
        ARTIFACT_EVENTS,
    }
)


# ---------------------------------------------------------------------------
# Tier 2 — Latent layer topics
# Additive. Never remove or rename a Tier 1 topic.
# ---------------------------------------------------------------------------

# -- Spor 1: Runtime track --------------------------------------------------
# These topics carry JEPA contracts from core/ on the production path.
# Producers and consumers are introduced in Sprint A1 Part 2.

AGENT_OBSERVATIONS_RAW = "agent.observations.raw"
# Contract : core.observations.Observation  (raw, pre-normalization)
# Producers: latent encoder agent (Sprint A1 Part 2)
# Consumers: normalization stage (Sprint A1 Part 2)
# Track    : runtime — present in all environments

AGENT_OBSERVATIONS_NORMALIZED = "agent.observations.normalized"
# Contract : core.observations.Observation  (normalized)
# Producers: normalization stage (Sprint A1 Part 2)
# Consumers: latent encoder (Sprint A1 Part 2)
# Track    : runtime — present in all environments

AGENT_LATENT_PACKETS = "agent.latent_packets"
# Contract : core.latent_packet.LatentPacket
# Producers: latent encoder (Sprint A1 Part 2)
# Consumers: hypothesis builder (Sprint A2)
# Track    : runtime — present in all environments

# -- Spor 2: Learning track -------------------------------------------------
# These topics carry experimental data for the JEPA learning loop.
# They are NEVER production-critical; absence does not cause system failure.

EVAL_LATENT_PREDICTIONS = "eval.latent_predictions"
# Contract : (future) JEPA predictor output — defined in Sprint A2
# Producers: JEPA predictor (Sprint A2)
# Consumers: evaluation harness (Sprint A2)
# Track    : learning/eval only — may be absent in production

MEMORY_EPISODE_LESSONS = "memory.episode_lessons"
# Contract : (future) self-correction output — defined in Sprint A5
# Producers: self-correction loop (Sprint A5)
# Consumers: memory consolidation (Sprint A5)
# Track    : learning/eval only — may be absent in production

# Tier 2 grouped by track for enumeration and testing.
TIER_2_RUNTIME_TOPICS: frozenset[str] = frozenset(
    {
        AGENT_OBSERVATIONS_RAW,
        AGENT_OBSERVATIONS_NORMALIZED,
        AGENT_LATENT_PACKETS,
    }
)

TIER_2_LEARNING_TOPICS: frozenset[str] = frozenset(
    {
        EVAL_LATENT_PREDICTIONS,
        MEMORY_EPISODE_LESSONS,
    }
)

# -- Spor 3: Swarm track (Sprint S2) ----------------------------------------
# Collective-intelligence bus.  3 partitions, snappy compression, acks=all.
# Produced by SwarmDirectivePublisher; consumed by SwarmObserverConsumer.

SWARM_EVENTS = "swarm.events"
# Contract : core.swarm_event.SwarmEvent
# Producers: agent collective, supervisor (Sprint S2)
# Consumers: SwarmObserverConsumer (Sprint S2)
# Track    : runtime — present in all environments

SWARM_DIRECTIVES = "swarm.directives"
# Contract : core.swarm_event.SwarmDirective
# Producers: SwarmDirectivePublisher (Sprint S2)
# Consumers: agent collective, orchestrator (Sprint S2)
# Track    : runtime — present in all environments

TIER_2_SWARM_TOPICS: frozenset[str] = frozenset(
    {
        SWARM_EVENTS,
        SWARM_DIRECTIVES,
    }
)

# -- Spor 4: Crypto Intelligence signal track (Sprint CI1) -------------------
# Produced by CryptoIntelAgent; consumed by SupervisorAgent / trading stack.
# 3 partitions, acks=all, compression=snappy (same as production defaults).

SIGNAL_CRYPTO_WHALE = "signal.crypto.whale"
# Contract : crypto_intel signal — whale transactions > $1 M
# Producers: CryptoIntelAgent
# Consumers: SupervisorAgent, TradingAgent
# Track    : runtime — present in all environments

SIGNAL_CRYPTO_MARKET = "signal.crypto.market"
# Contract : crypto_intel signal — market snapshot + trending coins
# Producers: CryptoIntelAgent
# Consumers: SupervisorAgent, dashboards
# Track    : runtime — present in all environments

SIGNAL_CRYPTO_NEWPROJECT = "signal.crypto.newproject"
# Contract : crypto_intel signal — scored new token candidates (confidence ≥ 0.70)
# Producers: CryptoIntelAgent
# Consumers: SupervisorAgent, TradingAgent
# Track    : runtime — present in all environments

TIER_2_CRYPTO_TOPICS: frozenset[str] = frozenset(
    {
        SIGNAL_CRYPTO_WHALE,
        SIGNAL_CRYPTO_MARKET,
        SIGNAL_CRYPTO_NEWPROJECT,
    }
)

# -- Spor 5: Infrastructure signal track (Sprint I1) -------------------------
# Produced by KafkaProvisioner / AgentBootstrapUseCase.
# 3 partitions, acks=all, compression=snappy.

SIGNAL_INFRA_GAP = "signal.infra.gap"
# Contract : infra signal — GapDetectorAgent found missing topics/groups
# Producers: GapDetectorAgent
# Consumers: SupervisorAgent, ops dashboards
# Track    : runtime — present in all environments

SIGNAL_INFRA_BOOTSTRAP_COMPLETE = "signal.infra.bootstrap.complete"
# Contract : infra signal — AgentBootstrapUseCase succeeded
# Producers: AgentBootstrapUseCase
# Consumers: SupervisorAgent, AgentRegistry
# Track    : runtime — present in all environments

SIGNAL_INFRA_BOOTSTRAP_FAILED = "signal.infra.bootstrap.failed"
# Contract : infra signal — AgentBootstrapUseCase failed (mirrors DLQ)
# Producers: AgentBootstrapUseCase
# Consumers: SupervisorAgent, ops dashboards
# Track    : runtime — present in all environments

TIER_2_INFRA_TOPICS: frozenset[str] = frozenset(
    {
        SIGNAL_INFRA_GAP,
        SIGNAL_INFRA_BOOTSTRAP_COMPLETE,
        SIGNAL_INFRA_BOOTSTRAP_FAILED,
    }
)

# -- Spor 6: Deep analysis signal track (Sprint DA1) -------------------------
# Produced by DeepAnalysisAgent after candidate evaluation.
# 3 partitions, acks=all, compression=snappy.

SIGNAL_ANALYSIS_COMPLETE = "signal.analysis.complete"
# Contract : deep analysis result — enriched thesis for human review
# Producers: DeepAnalysisAgent
# Consumers: SupervisorAgent, dashboards
# Track    : runtime — present in all environments

SIGNAL_ANALYSIS_FAILED = "signal.analysis.failed"
# Contract : deep analysis failure — error signal for ops
# Producers: DeepAnalysisAgent
# Consumers: SupervisorAgent, ops dashboards
# Track    : runtime — present in all environments

TIER_2_ANALYSIS_TOPICS: frozenset[str] = frozenset(
    {
        SIGNAL_ANALYSIS_COMPLETE,
        SIGNAL_ANALYSIS_FAILED,
    }
)

TIER_2_TOPICS: frozenset[str] = (
    TIER_2_RUNTIME_TOPICS
    | TIER_2_LEARNING_TOPICS
    | TIER_2_SWARM_TOPICS
    | TIER_2_CRYPTO_TOPICS
    | TIER_2_INFRA_TOPICS
    | TIER_2_ANALYSIS_TOPICS
)

# -- Spor 7: Content workflow track (Sprint CW1) ------------------------------
# Produced by content agents; consumed by publisher, analytics, supervisor.
# 3 partitions, acks=all, compression=snappy.

CONTENT_OPPORTUNITY_DETECTED = "content.opportunity.detected"
# Contract : TrendScoutAgent scored opportunity (confidence ≥ 0.60)
# Producers: TrendScoutAgent
# Consumers: ContentOrchestratorAgent, CreativeBriefAgent

CONTENT_BRIEF_READY = "content.brief.ready"
# Contract : CreativeBriefAgent brief awaiting human approval
# Producers: CreativeBriefAgent
# Consumers: ContentOrchestratorAgent, SupervisorAgent (L7 gate)

CONTENT_BRIEF_APPROVED = "content.brief.approved"
# Contract : Human-approved brief ready for production
# Producers: CLI / SupervisorAgent (after human approve)
# Consumers: ClaudeVideoService, PublishingService

CONTENT_VIDEO_REQUEST = "content.video.request"
# Contract : Render job submitted to ClaudeVideoService
# Producers: ContentOrchestratorAgent
# Consumers: services/claude-video

CONTENT_VIDEO_COMPLETE = "content.video.complete"
# Contract : Render job done; artifact ref included
# Producers: services/claude-video
# Consumers: ContentOrchestratorAgent, PublishingService

CONTENT_PUBLISH_REQUEST = "content.publish.request"
# Contract : Publishing job with content + channel spec
# Producers: ContentOrchestratorAgent
# Consumers: services/publisher

CONTENT_PUBLISHED = "content.published"
# Contract : Confirmed publish with external platform ref
# Producers: services/publisher
# Consumers: AnalyticsSkill, SupervisorAgent, dashboards

CONTENT_PUBLISH_FAILED = "content.publish.failed"
# Contract : Publishing failure — error signal for ops
# Producers: services/publisher
# Consumers: SupervisorAgent, ops dashboards

TIER_2_CONTENT_TOPICS: frozenset[str] = frozenset(
    {
        CONTENT_OPPORTUNITY_DETECTED,
        CONTENT_BRIEF_READY,
        CONTENT_BRIEF_APPROVED,
        CONTENT_VIDEO_REQUEST,
        CONTENT_VIDEO_COMPLETE,
        CONTENT_PUBLISH_REQUEST,
        CONTENT_PUBLISHED,
        CONTENT_PUBLISH_FAILED,
    }
)

TIER_2_TOPICS: frozenset[str] = (
    TIER_2_RUNTIME_TOPICS
    | TIER_2_LEARNING_TOPICS
    | TIER_2_SWARM_TOPICS
    | TIER_2_CRYPTO_TOPICS
    | TIER_2_INFRA_TOPICS
    | TIER_2_ANALYSIS_TOPICS
    | TIER_2_CONTENT_TOPICS
)

# ---------------------------------------------------------------------------
# Full registry
# ---------------------------------------------------------------------------

ALL_TOPICS: frozenset[str] = TIER_1_TOPICS | TIER_2_TOPICS
