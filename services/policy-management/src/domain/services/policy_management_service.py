"""
Consolidated Policy Management Service

Integrates functionality from:
- policy/ (Policy definitions, constitution, governance, review queues)
- policy-validator/ (Policy validation and constraints)
- policy_bootstrap/ (Configuration and routing)

Provides unified policy platform with PostgreSQL persistence.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

from postgresql_policy_store import PostgreSQLPolicyStore

# Import consolidated modules
from policies.policy_engine import PolicyEngine
from validation.policy_validator import PolicyValidator
from bootstrap.policy_router import PolicyRouter
from infrastructure.governance_bus import GovernanceBus

logger = logging.getLogger(__name__)


class PolicyManagementService:
    """
    Consolidated policy management service

    Provides unified interface for:
    - Policy definitions and evolution (from policy/)
    - Constitution and governance management
    - Policy validation and constraints (from policy-validator/)
    - Review queues and approval gates
    - Policy adoption and scoring
    - Configuration and routing (from policy_bootstrap/)
    """

    def __init__(self, database_url: str, kafka_servers: str = "kafka:9092"):
        self.database_url = database_url
        self.kafka_servers = kafka_servers

        # Core components
        self.store: Optional[PostgreSQLPolicyStore] = None
        self.policy_engine: Optional[PolicyEngine] = None
        self.policy_validator: Optional[PolicyValidator] = None
        self.policy_router: Optional[PolicyRouter] = None
        self.governance_bus: Optional[GovernanceBus] = None

    async def initialize(self) -> None:
        """Initialize the policy management service"""
        try:
            # Initialize PostgreSQL store
            self.store = await PostgreSQLPolicyStore.create(self.database_url)
            logger.info("Policy management store initialized")

            # Initialize governance event bus
            self.governance_bus = GovernanceBus(
                kafka_servers=self.kafka_servers,
                group_id="policy-management"
            )
            await self.governance_bus.initialize()

            # Initialize consolidated modules
            self.policy_engine = PolicyEngine(self.store, self.governance_bus)
            self.policy_validator = PolicyValidator(self.store, self.governance_bus)
            self.policy_router = PolicyRouter(self.store)

            # Initialize all modules
            await asyncio.gather(
                self.policy_engine.initialize(),
                self.policy_validator.initialize(),
                self.policy_router.initialize(),
            )

            # Setup governance event handlers
            await self._setup_governance_handlers()

            # Start governance event consumer
            if self.governance_bus:
                self.governance_bus.start_consumer()

            logger.info("Policy management service initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize policy management service: {e}")
            raise

    async def _setup_governance_handlers(self) -> None:
        """Setup governance event handlers"""
        if not self.governance_bus:
            return

        # Policy lifecycle events
        self.governance_bus.register_handler("policy_created", self._handle_policy_created)
        self.governance_bus.register_handler("policy_updated", self._handle_policy_updated)
        self.governance_bus.register_handler("policy_adopted", self._handle_policy_adopted)

        # Review and approval events
        self.governance_bus.register_handler("review_submitted", self._handle_review_submitted)
        self.governance_bus.register_handler("approval_required", self._handle_approval_required)

        # Constitution events
        self.governance_bus.register_handler("constitution_updated", self._handle_constitution_updated)

        logger.info("Governance event handlers registered")

    # Event Handlers
    async def _handle_policy_created(self, payload: Dict) -> None:
        """Handle policy created event"""
        try:
            policy_id = payload.get("policy_id")
            logger.info(f"Policy created: {policy_id}")

            # Trigger automatic validation
            if self.policy_validator and policy_id:
                validation_result = await self.policy_validator.validate_policy(policy_id)
                if not validation_result.get("valid", False):
                    # Add to review queue for manual review
                    await self._add_to_review_queue(policy_id, "validation_failed", validation_result)

        except Exception as e:
            logger.error(f"Failed to handle policy created: {e}")

    async def _handle_policy_updated(self, payload: Dict) -> None:
        """Handle policy updated event"""
        try:
            policy_id = payload.get("policy_id")
            logger.info(f"Policy updated: {policy_id}")

            # Track evolution
            if self.policy_engine and policy_id:
                await self.policy_engine.track_evolution(policy_id, payload.get("changes", {}))

        except Exception as e:
            logger.error(f"Failed to handle policy updated: {e}")

    async def _handle_policy_adopted(self, payload: Dict) -> None:
        """Handle policy adopted event"""
        try:
            policy_id = payload.get("policy_id")
            context_id = payload.get("context_id")
            score = payload.get("score")

            logger.info(f"Policy adopted: {policy_id} in context {context_id}")

            # Record adoption metrics
            if self.store and policy_id and context_id:
                await self.store.record_adoption(
                    adoption_id=f"{policy_id}_{context_id}_{datetime.utcnow().isoformat()}",
                    policy_id=policy_id,
                    context_id=context_id,
                    adoption_status="adopted",
                    score=score,
                    adoption_data=payload
                )

        except Exception as e:
            logger.error(f"Failed to handle policy adopted: {e}")

    async def _handle_review_submitted(self, payload: Dict) -> None:
        """Handle review submitted event"""
        try:
            review_id = payload.get("review_id")
            logger.info(f"Review submitted: {review_id}")

        except Exception as e:
            logger.error(f"Failed to handle review submitted: {e}")

    async def _handle_approval_required(self, payload: Dict) -> None:
        """Handle approval required event"""
        try:
            policy_id = payload.get("policy_id")
            approval_type = payload.get("approval_type")

            logger.info(f"Approval required: {policy_id} ({approval_type})")

            # Add to appropriate approval queue
            if self.store:
                await self._add_to_review_queue(policy_id, approval_type, payload)

        except Exception as e:
            logger.error(f"Failed to handle approval required: {e}")

    async def _handle_constitution_updated(self, payload: Dict) -> None:
        """Handle constitution updated event"""
        try:
            constitution_id = payload.get("constitution_id")
            logger.info(f"Constitution updated: {constitution_id}")

            # Trigger re-validation of all policies against new constitution
            if self.policy_validator:
                await self.policy_validator.revalidate_all_policies()

        except Exception as e:
            logger.error(f"Failed to handle constitution updated: {e}")

    async def _add_to_review_queue(self, policy_id: str, review_type: str, review_data: Dict) -> None:
        """Add policy to review queue"""
        if not self.store:
            return

        review_id = f"review_{policy_id}_{review_type}_{datetime.utcnow().isoformat()}"
        await self.store.add_to_review_queue(review_id, policy_id, review_type, review_data)

    # Policy Engine Interface (from policy/)
    async def create_policy(self, policy_id: str, policy_name: str,
                          policy_type: str, policy_content: Dict,
                          version: str = "1.0", metadata: Optional[Dict] = None) -> None:
        """Create a new policy definition"""
        return await self.policy_engine.create_policy(
            policy_id, policy_name, policy_type, policy_content, version, metadata
        )

    async def get_policy(self, policy_id: str) -> Optional[Dict]:
        """Get policy definition by ID"""
        return await self.policy_engine.get_policy(policy_id)

    async def list_policies(self, policy_type: Optional[str] = None) -> List[Dict]:
        """List policies, optionally filtered by type"""
        return await self.policy_engine.list_policies(policy_type)

    async def update_policy(self, policy_id: str, updates: Dict) -> None:
        """Update an existing policy"""
        return await self.policy_engine.update_policy(policy_id, updates)

    async def evolve_policy(self, policy_id: str, evolution_type: str,
                          changes: Dict, reason: str = "") -> str:
        """Evolve a policy to a new version"""
        return await self.policy_engine.evolve_policy(policy_id, evolution_type, changes, reason)

    # Constitution Management (from policy/)
    async def create_constitution(self, constitution_id: str, constitution_name: str,
                                constitution_content: Dict, version: str = "1.0") -> None:
        """Create or update constitution"""
        return await self.policy_engine.create_constitution(
            constitution_id, constitution_name, constitution_content, version
        )

    async def get_active_constitution(self) -> Optional[Dict]:
        """Get the currently active constitution"""
        return await self.policy_engine.get_active_constitution()

    async def activate_constitution(self, constitution_id: str) -> None:
        """Activate a constitution version"""
        return await self.policy_engine.activate_constitution(constitution_id)

    # Policy Validation Interface (from policy-validator/)
    async def validate_policy(self, policy_id: str) -> Dict:
        """Validate a policy against rules and constitution"""
        return await self.policy_validator.validate_policy(policy_id)

    async def create_validation_rule(self, rule_id: str, rule_name: str,
                                   rule_type: str, rule_content: Dict,
                                   target_policies: List[str], severity: str = "error") -> None:
        """Create a validation rule"""
        return await self.policy_validator.create_validation_rule(
            rule_id, rule_name, rule_type, rule_content, target_policies, severity
        )

    async def get_validation_rules(self, policy_type: Optional[str] = None) -> List[Dict]:
        """Get validation rules for a policy type"""
        return await self.policy_validator.get_validation_rules(policy_type)

    async def validate_against_constitution(self, policy_content: Dict) -> Dict:
        """Validate policy content against constitution"""
        return await self.policy_validator.validate_against_constitution(policy_content)

    # Review Queue Management (from policy/)
    async def get_pending_reviews(self, review_type: Optional[str] = None) -> List[Dict]:
        """Get pending policy reviews"""
        if not self.store:
            return []
        return await self.store.get_pending_reviews(review_type)

    async def submit_review(self, review_id: str, reviewer_id: str,
                          review_result: Dict) -> None:
        """Submit a policy review"""
        return await self.policy_engine.submit_review(review_id, reviewer_id, review_result)

    async def approve_policy(self, policy_id: str, approver_id: str,
                           approval_data: Dict) -> None:
        """Approve a policy"""
        return await self.policy_engine.approve_policy(policy_id, approver_id, approval_data)

    # Approval Gates Management (from policy/)
    async def create_approval_gate(self, gate_id: str, gate_name: str,
                                 gate_type: str, gate_config: Dict, policies: List[str]) -> None:
        """Create an approval gate"""
        return await self.policy_engine.create_approval_gate(
            gate_id, gate_name, gate_type, gate_config, policies
        )

    async def get_approval_gates(self, policy_id: Optional[str] = None) -> List[Dict]:
        """Get approval gates"""
        if not self.store:
            return []
        return await self.store.get_approval_gates(policy_id)

    async def check_approval_gates(self, policy_id: str) -> List[Dict]:
        """Check if policy passes approval gates"""
        return await self.policy_engine.check_approval_gates(policy_id)

    # Policy Adoption and Scoring (from policy/)
    async def score_policy(self, policy_id: str, context_data: Dict) -> float:
        """Score a policy for adoption in a context"""
        return await self.policy_engine.score_policy(policy_id, context_data)

    async def adopt_policy(self, policy_id: str, context_id: str,
                         adoption_data: Optional[Dict] = None) -> str:
        """Adopt a policy in a context"""
        return await self.policy_engine.adopt_policy(policy_id, context_id, adoption_data)

    async def get_adoption_history(self, policy_id: str) -> List[Dict]:
        """Get adoption history for a policy"""
        return await self.policy_engine.get_adoption_history(policy_id)

    # Configuration and Routing (from policy_bootstrap/)
    async def get_policy_configuration(self) -> Dict:
        """Get policy management configuration"""
        return await self.policy_router.get_configuration()

    async def update_configuration(self, config_updates: Dict) -> None:
        """Update policy management configuration"""
        return await self.policy_router.update_configuration(config_updates)

    async def route_policy_request(self, request_type: str, request_data: Dict) -> Dict:
        """Route policy management request"""
        return await self.policy_router.route_request(request_type, request_data)

    # Specialized Policy Services (from policy/ extensive functionality)
    async def process_policy_evolution_queue(self) -> List[Dict]:
        """Process pending policy evolutions"""
        return await self.policy_engine.process_evolution_queue()

    async def run_governance_smoke_tests(self) -> Dict:
        """Run governance smoke tests"""
        return await self.policy_engine.run_governance_smoke_tests()

    async def analyze_policy_performance(self, policy_id: str,
                                       timeframe_days: int = 30) -> Dict:
        """Analyze policy performance metrics"""
        return await self.policy_engine.analyze_policy_performance(policy_id, timeframe_days)

    async def suggest_policy_improvements(self, policy_id: str) -> List[Dict]:
        """Suggest improvements for a policy"""
        return await self.policy_engine.suggest_improvements(policy_id)

    # Platform Status
    async def get_platform_status(self) -> Dict:
        """Get policy management platform status"""
        try:
            base_status = {
                "status": "healthy",
                "modules": {},
                "statistics": {}
            }

            # Module status
            if self.policy_engine:
                base_status["modules"]["engine"] = self.policy_engine.is_healthy()
            if self.policy_validator:
                base_status["modules"]["validator"] = self.policy_validator.is_healthy()
            if self.policy_router:
                base_status["modules"]["router"] = self.policy_router.is_healthy()

            # Statistics
            all_policies = await self.list_policies()
            base_status["statistics"]["total_policies"] = len(all_policies)
            base_status["statistics"]["pending_reviews"] = len(await self.get_pending_reviews())

            # Policy type breakdown
            policy_types = {}
            for policy in all_policies:
                policy_type = policy.get("policy_type", "unknown")
                policy_types[policy_type] = policy_types.get(policy_type, 0) + 1
            base_status["statistics"]["policy_types"] = policy_types

            return base_status

        except Exception as e:
            logger.error(f"Failed to get platform status: {e}")
            return {"status": "unhealthy", "error": str(e)}

    async def shutdown(self) -> None:
        """Shutdown the policy management service"""
        try:
            # Stop governance event consumer
            if self.governance_bus:
                self.governance_bus.stop_consumer()

            # Shutdown modules
            if self.policy_engine:
                await self.policy_engine.shutdown()
            if self.policy_validator:
                await self.policy_validator.shutdown()
            if self.policy_router:
                await self.policy_router.shutdown()

            # Shutdown infrastructure
            if self.governance_bus:
                await self.governance_bus.shutdown()

            # Close store
            if self.store:
                await self.store.close()

            logger.info("Policy management service shutdown complete")

        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            raise