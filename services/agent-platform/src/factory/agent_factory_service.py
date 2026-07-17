"""
Phase 7 Agent Factory Service

LangGraph-based factory implementing Archon-inspired state machine for generating
agent rosters and dynamic policies. Implements Stage 1 (classify-only mode) functionality.
"""

import logging
import asyncio
from typing import Dict, Any, Optional, List, Literal
from datetime import datetime
from dataclasses import dataclass, asdict

from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint import MemorySaver
from langgraph.prebuilt import ToolExecutor

from .state_machine import FactoryState, IntentClassification, RosterConfig, PolicyConfig
from .intent_classifier import IntentClassifier
from .policy_generator import PolicyGenerator
from .roster_designer import RosterDesigner

logger = logging.getLogger(__name__)


class AgentFactoryService:
    """
    Phase 7 Agent Factory - Archon-inspired meta-service that generates both
    agent rosters and effective policies per task.

    Stage 1: Operates in classify-only mode for shadow deployment.
    """

    def __init__(self, store, event_bus, config: Optional[Dict] = None):
        self.store = store
        self.event_bus = event_bus
        self.config = config or {}

        # Factory components
        self.intent_classifier = IntentClassifier(config.get("classifier", {}))
        self.policy_generator = PolicyGenerator(config.get("policy", {}))
        self.roster_designer = RosterDesigner(config.get("roster", {}))

        # LangGraph state machine
        self.state_graph = None
        self.memory_saver = MemorySaver()

        # Stage 1: classify-only mode flag
        self.classify_only_mode = config.get("classify_only_mode", True)

        logger.info("Agent Factory Service initialized in Stage 1 (classify-only) mode")

    async def initialize(self) -> None:
        """Initialize the factory service and LangGraph state machine"""
        try:
            # Initialize components
            await self.intent_classifier.initialize()
            await self.policy_generator.initialize()
            await self.roster_designer.initialize()

            # Build state machine
            self._build_state_machine()

            # Initialize base + 6 profiles for Stage 1
            await self._deploy_stage1_profiles()

            logger.info("Agent Factory Service initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Agent Factory Service: {e}")
            raise

    def _build_state_machine(self) -> None:
        """Build the LangGraph state machine for factory operations"""

        workflow = StateGraph(FactoryState)

        # Add nodes for the 6-step factory process
        workflow.add_node("classify_intent", self._classify_intent_node)
        workflow.add_node("reason_scope", self._reason_scope_node)
        workflow.add_node("policy_scope", self._policy_scope_node)
        workflow.add_node("advise", self._advise_node)
        workflow.add_node("draft_roster", self._draft_roster_node)
        workflow.add_node("draft_policy", self._draft_policy_node)
        workflow.add_node("validate_policy", self._validate_policy_node)
        workflow.add_node("coherence_check", self._coherence_check_node)

        # Define workflow edges
        workflow.add_edge(START, "classify_intent")
        workflow.add_edge("classify_intent", "reason_scope")
        workflow.add_edge("reason_scope", "policy_scope")
        workflow.add_edge("policy_scope", "advise")

        # Parallel drafting
        workflow.add_edge("advise", "draft_roster")
        workflow.add_edge("advise", "draft_policy")

        # Wait for both drafts before validation
        workflow.add_edge("draft_roster", "validate_policy")
        workflow.add_edge("draft_policy", "validate_policy")

        workflow.add_edge("validate_policy", "coherence_check")
        workflow.add_edge("coherence_check", END)

        self.state_graph = workflow.compile(checkpointer=self.memory_saver)

        logger.info("LangGraph state machine compiled successfully")

    # === PUBLIC API ===

    async def classify(self, user_prompt: str, caller_context: Dict) -> Dict:
        """
        Stage 1: Classify intent only (shadow mode)

        This is the main entrypoint for request-gate integration.
        Returns classification without full roster/policy generation.
        """
        try:
            # Create initial state
            state = FactoryState(
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

            # Run classification only
            result = await self._classify_intent_node(state)

            # Log for Stage 1 divergence analysis
            await self._log_classification_result(user_prompt, caller_context, result)

            # Emit Stage 1 event
            await self.event_bus.emit("factory.classification", {
                "session_id": caller_context.get("session_id"),
                "user_prompt": user_prompt,
                "classification": result.get("intent"),
                "confidence": result.get("intent", {}).get("confidence", 0.0),
                "stage": "stage1_shadow",
                "timestamp": datetime.utcnow().isoformat()
            })

            return {
                "classification": result.get("intent"),
                "would_route": self._determine_would_route(result.get("intent")),
                "stage": "stage1_classify_only"
            }

        except Exception as e:
            logger.error(f"Classification failed: {e}")
            return {
                "classification": None,
                "would_route": "readonly",  # Safe default
                "error": str(e),
                "stage": "stage1_classify_only"
            }

    async def design_roster(self, user_prompt: str, caller_context: Dict) -> Dict:
        """
        Full factory pipeline for roster and policy generation.
        Not used in Stage 1, placeholder for Stage 2+.
        """
        if self.classify_only_mode:
            logger.warning("design_roster called in classify-only mode, returning classification only")
            return await self.classify(user_prompt, caller_context)

        # Stage 2+ implementation would go here
        raise NotImplementedError("Full design_roster available in Stage 2+")

    # === STATE MACHINE NODES ===

    async def _classify_intent_node(self, state: FactoryState) -> FactoryState:
        """Node: Classify user intent using hybrid classifier"""
        try:
            intent = await self.intent_classifier.classify_intent(
                state.user_prompt,
                state.caller_context
            )

            state.intent = intent
            logger.debug(f"Intent classified: {intent.profile} (confidence: {intent.confidence})")

            return state

        except Exception as e:
            logger.error(f"Intent classification failed: {e}")
            # Safe fallback to readonly
            state.intent = IntentClassification(
                profile="readonly",
                complexity="standard",
                confidence=0.0,
                requires_clarification=False,
                clarification_question=None
            )
            return state

    async def _reason_scope_node(self, state: FactoryState) -> FactoryState:
        """Node: Generate scope.md for task understanding"""
        # Stage 1: Placeholder - not used in classify-only mode
        state.scope = f"Task scope analysis for: {state.user_prompt[:100]}..."
        return state

    async def _policy_scope_node(self, state: FactoryState) -> FactoryState:
        """Node: Generate policy_scope.md for policy requirements"""
        # Stage 1: Placeholder - not used in classify-only mode
        state.policy_scope = f"Policy scope for {state.intent.profile} profile"
        return state

    async def _advise_node(self, state: FactoryState) -> FactoryState:
        """Node: Retrieve templates from agent-resources and policy-resources"""
        # Stage 1: Placeholder - not used in classify-only mode
        state.advisor_output = f"Advisory output for {state.intent.profile}"
        return state

    async def _draft_roster_node(self, state: FactoryState) -> FactoryState:
        """Node: Draft agent roster based on classified intent"""
        # Stage 1: Placeholder - not used in classify-only mode
        state.draft_roster = None
        return state

    async def _draft_policy_node(self, state: FactoryState) -> FactoryState:
        """Node: Draft policy YAML based on profile and context"""
        # Stage 1: Placeholder - not used in classify-only mode
        state.draft_policy = None
        return state

    async def _validate_policy_node(self, state: FactoryState) -> FactoryState:
        """Node: Validate policy against constraints (deterministic)"""
        # Stage 1: Placeholder - not used in classify-only mode
        state.policy_validation = None
        return state

    async def _coherence_check_node(self, state: FactoryState) -> FactoryState:
        """Node: Check coherence between roster and policy"""
        # Stage 1: Placeholder - not used in classify-only mode
        state.coherence_report = None
        return state

    # === STAGE 1 SPECIFIC METHODS ===

    async def _deploy_stage1_profiles(self) -> None:
        """Deploy base + 6 profiles for Stage 1 shadow deployment"""
        try:
            profiles = [
                "base", "readonly", "research", "editor",
                "builder", "operator", "admin_assisted"
            ]

            for profile in profiles:
                await self.policy_generator.deploy_profile(profile)

            logger.info(f"Deployed {len(profiles)} profiles for Stage 1")

        except Exception as e:
            logger.error(f"Failed to deploy Stage 1 profiles: {e}")
            raise

    def _determine_would_route(self, intent: Optional[IntentClassification]) -> str:
        """Determine what profile the request would be routed to"""
        if not intent:
            return "readonly"  # Safe default

        # Confidence-based routing per Phase 7 plan
        if intent.confidence > 0.85:
            return intent.profile
        elif intent.confidence >= 0.5:
            return "readonly"  # Safe default for medium confidence
        else:
            return "readonly"  # Safe default for low confidence

    async def _log_classification_result(self, prompt: str, context: Dict, result: Dict) -> None:
        """Log classification result for Stage 1 divergence analysis"""
        try:
            # This will be used to compare (would-route, actual-route) in Stage 1
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "session_id": context.get("session_id"),
                "user_id": context.get("user_id"),
                "prompt_hash": hash(prompt),  # Don't log full prompt for privacy
                "would_route": self._determine_would_route(result.get("intent")),
                "confidence": result.get("intent", {}).get("confidence", 0.0),
                "requires_clarification": result.get("intent", {}).get("requires_clarification", False),
                "stage": "stage1_shadow"
            }

            # Store in database for divergence analysis
            await self.store.log_classification_result(log_entry)

        except Exception as e:
            logger.error(f"Failed to log classification result: {e}")

    # === HEALTH & LIFECYCLE ===

    def is_healthy(self) -> bool:
        """Check factory health"""
        return (
            self.intent_classifier is not None and
            self.policy_generator is not None and
            self.roster_designer is not None and
            self.state_graph is not None
        )

    async def get_statistics(self) -> Dict:
        """Get factory statistics for Stage 1"""
        try:
            # Get classification statistics
            classification_stats = await self.intent_classifier.get_statistics()

            return {
                "stage": "stage1_shadow",
                "mode": "classify_only",
                "classifications_today": classification_stats.get("today", 0),
                "confidence_distribution": classification_stats.get("confidence_dist", {}),
                "profiles_deployed": ["base", "readonly", "research", "editor", "builder", "operator", "admin_assisted"],
                "health": self.is_healthy()
            }

        except Exception as e:
            logger.error(f"Failed to get statistics: {e}")
            return {"error": str(e)}

    async def shutdown(self) -> None:
        """Shutdown factory service"""
        try:
            await self.intent_classifier.shutdown()
            await self.policy_generator.shutdown()
            await self.roster_designer.shutdown()

            logger.info("Agent Factory Service shutdown complete")

        except Exception as e:
            logger.error(f"Error during factory shutdown: {e}")
            raise