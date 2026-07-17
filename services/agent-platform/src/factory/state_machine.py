"""
Phase 7 Factory State Machine Types

LangGraph TypedDict definitions for the factory state machine.
Follows Phase 7 specification for FactoryState and related types.
"""

from typing import TypedDict, Optional, Dict, Any, List, Literal
from datetime import datetime
from dataclasses import dataclass, asdict
from enum import Enum


class ComplexityLevel(str, Enum):
    """Task complexity levels that map to agent counts"""
    TRIVIAL = "trivial"      # max_agents: 1
    STANDARD = "standard"    # max_agents: 3-5
    COMPLEX = "complex"      # max_agents: up to 10


class RiskTier(str, Enum):
    """Risk tiers for profiles and contexts"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ProfileType(str, Enum):
    """Standard profile types (6 whitelisted profiles)"""
    READONLY = "readonly"
    RESEARCH = "research"
    EDITOR = "editor"
    BUILDER = "builder"
    OPERATOR = "operator"
    ADMIN_ASSISTED = "admin_assisted"


@dataclass
class IntentClassification:
    """Structured output from intent classification"""
    profile: str                            # One of 6 whitelisted profiles
    complexity: ComplexityLevel            # Maps to max_agents constraint
    confidence: float                      # 0.0-1.0, drives routing logic
    requires_clarification: bool           # If confidence < 0.5
    clarification_question: Optional[str]  # Generated question if needed
    detected_capabilities: Optional[List[str]] = None  # Detected capability requirements
    risk_hints: Optional[List[str]] = None  # Risk indicators from prompt


@dataclass
class AgentSpec:
    """Agent specification within a roster"""
    agent_type: str                        # Type from agent registry
    agent_name: str                        # Human-readable name
    tools: List[str]                      # Required tools/capabilities
    model: str                            # LLM model to use
    config: Dict[str, Any]                # Agent-specific configuration
    dependencies: Optional[List[str]] = None  # Other agents this depends on


@dataclass
class RosterConfig:
    """Complete agent roster configuration"""
    max_agents: int                        # From complexity mapping
    max_depth: int                         # Spawning depth limit
    coordination: Literal["linear", "supervisor"] = "linear"  # Default to linear
    agents: List[AgentSpec] = None         # Agent specifications
    execution_order: Optional[List[str]] = None  # Execution dependencies


@dataclass
class CapabilityConstraint:
    """Constraint definition for a capability"""
    effect: Literal["allow", "deny"]
    reason: Optional[str] = None           # Reason for deny
    constraints: Optional[Dict[str, Any]] = None  # Additional constraints
    sealed: bool = False                   # Prevents override by lower layers


@dataclass
class PolicyBudget:
    """Resource budget constraints"""
    token_budget: int                      # Max LLM tokens
    wallclock_seconds: int                 # Max execution time
    cost_usd: float                        # Max cost in USD
    max_agents: Optional[int] = None       # Max concurrent agents
    max_depth: Optional[int] = None        # Max spawning depth


@dataclass
class PolicyObligation:
    """Policy obligation (audit, rate limiting, etc.)"""
    type: str                              # "audit_log", "rate_limit", etc.
    fields: Optional[List[str]] = None     # Fields to log
    capability: Optional[str] = None       # For rate limiting
    per_session: Optional[int] = None      # Rate limit per session


@dataclass
class PolicyConfig:
    """Complete policy configuration (YAML-shaped)"""
    api_version: str = "babyai.policy/v1"
    kind: str = "PolicySet"
    metadata: Dict[str, Any] = None        # id, layer, parent, version, risk_tier
    defaults: Dict[str, str] = None        # Default effect
    capabilities: Dict[str, CapabilityConstraint] = None
    obligations: List[PolicyObligation] = None
    budget: PolicyBudget = None


@dataclass
class ValidationReport:
    """Policy validation result"""
    valid: bool
    errors: List[str]
    warnings: List[str]
    capability_attenuation_ok: bool        # Child ⊆ parent check
    budget_valid: bool                     # Budget constraint validation


@dataclass
class CoherenceReport:
    """Coherence check result between roster and policy"""
    coherent: bool
    issues: List[str]                      # List of coherence violations
    tool_policy_mismatches: List[str]      # Tools not allowed by policy
    spawn_depth_violations: List[str]      # Depth violations


@dataclass
class FactoryBundle:
    """Complete output bundle from factory"""
    roster: RosterConfig
    policy: PolicyConfig
    manifest: Dict[str, Any]               # Metadata about the generation
    tests: Optional[Dict[str, Any]] = None # Generated test cases


class FactoryState(TypedDict):
    """
    LangGraph state for the factory state machine

    Flows through 6 nodes:
    classify_intent → reason_scope → policy_scope → advise →
    [draft_roster + draft_policy] → validate_policy → coherence_check
    """
    # Input
    user_prompt: str                       # Original user request
    caller_context: Dict[str, Any]         # session_id, user_id, repo_root, etc.

    # Classification
    intent: Optional[IntentClassification]  # From classify_intent node

    # Scope definition
    scope: str                             # scope.md content
    policy_scope: str                      # policy_scope.md content

    # Advisory
    advisor_output: str                    # Template selections

    # Drafting
    draft_roster: Optional[RosterConfig]   # From draft_roster node
    draft_policy: Optional[PolicyConfig]   # From draft_policy node

    # Validation
    policy_validation: Optional[ValidationReport]  # From validate_policy node
    coherence_report: Optional[CoherenceReport]    # From coherence_check node

    # Human in the loop
    human_approval: Literal["auto", "requested", "granted", "denied"]

    # Final output
    final_bundle: Optional[FactoryBundle]   # Complete factory output


# === HELPER FUNCTIONS ===

def create_initial_state(user_prompt: str, caller_context: Dict[str, Any]) -> FactoryState:
    """Create initial factory state"""
    return FactoryState(
        user_prompt=user_prompt,
        caller_context=caller_context,
        intent=None,
        scope="",
        policy_scope="",
        advisor_output="",
        draft_roster=None,
        draft_policy=None,
        policy_validation=None,
        coherence_report=None,
        human_approval="auto",
        final_bundle=None
    )


def serialize_state(state: FactoryState) -> Dict[str, Any]:
    """Serialize state for storage/logging"""
    serialized = {}

    for key, value in state.items():
        if isinstance(value, (IntentClassification, RosterConfig, PolicyConfig,
                             ValidationReport, CoherenceReport, FactoryBundle)):
            serialized[key] = asdict(value)
        else:
            serialized[key] = value

    return serialized


def complexity_to_max_agents(complexity: ComplexityLevel) -> int:
    """Map complexity level to max agents constraint"""
    mapping = {
        ComplexityLevel.TRIVIAL: 1,
        ComplexityLevel.STANDARD: 5,
        ComplexityLevel.COMPLEX: 10
    }
    return mapping[complexity]


def profile_to_risk_tier(profile: str) -> RiskTier:
    """Map profile to default risk tier"""
    mapping = {
        "readonly": RiskTier.LOW,
        "research": RiskTier.LOW,
        "editor": RiskTier.MEDIUM,
        "builder": RiskTier.HIGH,
        "operator": RiskTier.HIGH,
        "admin_assisted": RiskTier.CRITICAL
    }
    return mapping.get(profile, RiskTier.MEDIUM)