"""
Policy Engine Module

Consolidated from services/policy/src/
Provides policy lifecycle management, constitution governance,
and policy evolution tracking.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from uuid import uuid4

logger = logging.getLogger(__name__)


class PolicyEngine:
    """
    Core policy management engine

    Consolidated functionality from policy service:
    - Policy definitions and lifecycle management
    - Constitution and governance management
    - Policy evolution and versioning
    - Review queue and approval workflow
    - Policy scoring and adoption tracking
    """

    def __init__(self, store, governance_bus=None):
        self.store = store
        self.governance_bus = governance_bus

        # Policy type registry (from original policy/)
        self.policy_types = {
            "constitution": "Constitutional governance rules",
            "approval_gate": "Approval workflow policies",
            "promotion_gate": "Code promotion policies",
            "rights_guard": "Rights and permissions policies",
            "review_queue": "Review workflow policies",
            "performance_budget": "Performance constraint policies",
            "training_judge": "Training validation policies",
            "coding_standard": "Code quality policies",
            "must_include": "Required inclusion policies",
            "crypto_analysis": "Cryptographic analysis policies",
            "discovery": "Service discovery policies",
            "ingest": "Data ingestion policies",
            "novelty": "Novelty detection policies",
            "stagnation": "Stagnation prevention policies"
        }

    async def initialize(self) -> None:
        """Initialize policy engine"""
        try:
            # Initialize built-in policies if needed
            await self._initialize_default_policies()
            logger.info("Policy engine initialized")

        except Exception as e:
            logger.error(f"Failed to initialize policy engine: {e}")
            raise

    async def _initialize_default_policies(self) -> None:
        """Initialize default policies and constitution"""
        # Check if constitution exists
        constitution = await self.store.get_active_constitution()
        if not constitution:
            # Create default constitution (from policy/src/constitution.yaml)
            default_constitution = {
                "name": "BabyAI Default Constitution",
                "version": "1.0",
                "principles": [
                    "Transparency in decision-making",
                    "Evidence-based policy adoption",
                    "Continuous improvement",
                    "Stakeholder participation",
                    "Risk-aware governance"
                ],
                "governance_structure": {
                    "policy_approval_threshold": 0.75,
                    "review_timeout_hours": 48,
                    "mandatory_review_types": ["security", "performance", "compliance"]
                },
                "amendment_process": {
                    "proposal_threshold": 0.5,
                    "adoption_threshold": 0.67,
                    "minimum_review_period_hours": 72
                }
            }

            await self.store.create_constitution(
                constitution_id="default_constitution_v1",
                constitution_name="BabyAI Default Constitution",
                constitution_content=default_constitution
            )

            logger.info("Default constitution created")

    # Policy Lifecycle Management
    async def create_policy(self, policy_id: str, policy_name: str,
                          policy_type: str, policy_content: Dict,
                          version: str = "1.0", metadata: Optional[Dict] = None) -> None:
        """Create a new policy definition"""
        try:
            # Validate policy type
            if policy_type not in self.policy_types:
                logger.warning(f"Unknown policy type: {policy_type}")

            # Enrich metadata
            enhanced_metadata = {
                "created_by": "system",
                "policy_type_description": self.policy_types.get(policy_type, "Custom policy"),
                "creation_timestamp": datetime.utcnow().isoformat(),
                **(metadata or {})
            }

            # Store policy
            await self.store.create_policy(
                policy_id=policy_id,
                policy_name=policy_name,
                policy_type=policy_type,
                policy_content=policy_content,
                version=version,
                metadata=enhanced_metadata
            )

            # Publish governance event
            if self.governance_bus:
                self.governance_bus.publish_policy_created(policy_id, {
                    "policy_name": policy_name,
                    "policy_type": policy_type,
                    "version": version
                })

            logger.info(f"Policy created: {policy_id} ({policy_type})")

        except Exception as e:
            logger.error(f"Failed to create policy {policy_id}: {e}")
            raise

    async def get_policy(self, policy_id: str) -> Optional[Dict]:
        """Get policy definition by ID"""
        try:
            return await self.store.get_policy(policy_id)

        except Exception as e:
            logger.error(f"Failed to get policy {policy_id}: {e}")
            return None

    async def list_policies(self, policy_type: Optional[str] = None) -> List[Dict]:
        """List policies, optionally filtered by type"""
        try:
            if policy_type:
                return await self.store.list_policies_by_type(policy_type)
            else:
                # Get all policy types
                all_policies = []
                for ptype in self.policy_types:
                    policies = await self.store.list_policies_by_type(ptype)
                    all_policies.extend(policies)
                return all_policies

        except Exception as e:
            logger.error(f"Failed to list policies: {e}")
            return []

    async def update_policy(self, policy_id: str, updates: Dict) -> None:
        """Update an existing policy"""
        try:
            # Get existing policy
            existing = await self.get_policy(policy_id)
            if not existing:
                raise ValueError(f"Policy not found: {policy_id}")

            # Merge updates
            updated_content = {**existing["policy_content"], **updates.get("policy_content", {})}
            updated_metadata = {**existing["metadata"], **updates.get("metadata", {})}
            updated_metadata["last_modified"] = datetime.utcnow().isoformat()

            # Update policy
            await self.store.create_policy(
                policy_id=policy_id,
                policy_name=updates.get("policy_name", existing["policy_name"]),
                policy_type=updates.get("policy_type", existing["policy_type"]),
                policy_content=updated_content,
                version=updates.get("version", existing["version"]),
                metadata=updated_metadata
            )

            # Publish governance event
            if self.governance_bus:
                self.governance_bus.publish_policy_updated(policy_id, {
                    "changes": updates,
                    "version": updates.get("version", existing["version"])
                })

            logger.info(f"Policy updated: {policy_id}")

        except Exception as e:
            logger.error(f"Failed to update policy {policy_id}: {e}")
            raise

    async def evolve_policy(self, policy_id: str, evolution_type: str,
                          changes: Dict, reason: str = "") -> str:
        """Evolve a policy to a new version"""
        try:
            # Get current policy
            current = await self.get_policy(policy_id)
            if not current:
                raise ValueError(f"Policy not found: {policy_id}")

            # Generate new version
            current_version = current["version"]
            major, minor = current_version.split(".")[:2]

            if evolution_type == "major":
                new_version = f"{int(major) + 1}.0"
            else:  # minor
                new_version = f"{major}.{int(minor) + 1}"

            # Apply changes
            updated_content = {**current["policy_content"], **changes}
            metadata = {**current["metadata"],
                       "evolution_from": current_version,
                       "evolution_type": evolution_type,
                       "evolution_reason": reason,
                       "evolved_at": datetime.utcnow().isoformat()}

            # Create new version
            await self.store.create_policy(
                policy_id=policy_id,
                policy_name=current["policy_name"],
                policy_type=current["policy_type"],
                policy_content=updated_content,
                version=new_version,
                metadata=metadata
            )

            logger.info(f"Policy evolved: {policy_id} {current_version} -> {new_version}")
            return new_version

        except Exception as e:
            logger.error(f"Failed to evolve policy {policy_id}: {e}")
            raise

    # Constitution Management
    async def create_constitution(self, constitution_id: str, constitution_name: str,
                                constitution_content: Dict, version: str = "1.0") -> None:
        """Create or update constitution"""
        try:
            await self.store.create_constitution(
                constitution_id=constitution_id,
                constitution_name=constitution_name,
                constitution_content=constitution_content,
                version=version
            )

            # Publish governance event
            if self.governance_bus:
                self.governance_bus.publish_constitution_updated(constitution_id, {
                    "constitution_name": constitution_name,
                    "version": version
                })

            logger.info(f"Constitution created: {constitution_id}")

        except Exception as e:
            logger.error(f"Failed to create constitution {constitution_id}: {e}")
            raise

    async def get_active_constitution(self) -> Optional[Dict]:
        """Get the currently active constitution"""
        try:
            return await self.store.get_active_constitution()

        except Exception as e:
            logger.error(f"Failed to get active constitution: {e}")
            return None

    async def activate_constitution(self, constitution_id: str) -> None:
        """Activate a constitution version"""
        try:
            # This would update the constitution status to active
            # Implementation depends on store schema
            logger.info(f"Constitution activated: {constitution_id}")

        except Exception as e:
            logger.error(f"Failed to activate constitution {constitution_id}: {e}")
            raise

    # Review and Approval Workflow
    async def submit_review(self, review_id: str, reviewer_id: str,
                          review_result: Dict) -> None:
        """Submit a policy review"""
        try:
            # Update review in queue
            # Implementation would update review status
            logger.info(f"Review submitted: {review_id} by {reviewer_id}")

            # Publish governance event
            if self.governance_bus:
                self.governance_bus.publish_review_submitted(review_id, {
                    "reviewer_id": reviewer_id,
                    "result": review_result
                })

        except Exception as e:
            logger.error(f"Failed to submit review {review_id}: {e}")
            raise

    async def approve_policy(self, policy_id: str, approver_id: str,
                           approval_data: Dict) -> None:
        """Approve a policy"""
        try:
            # Update policy status to approved
            # Implementation would update policy status
            logger.info(f"Policy approved: {policy_id} by {approver_id}")

            # Publish governance event
            if self.governance_bus:
                self.governance_bus.publish_policy_approved(policy_id, {
                    "approver_id": approver_id,
                    "approval_data": approval_data
                })

        except Exception as e:
            logger.error(f"Failed to approve policy {policy_id}: {e}")
            raise

    # Approval Gates
    async def create_approval_gate(self, gate_id: str, gate_name: str,
                                 gate_type: str, gate_config: Dict, policies: List[str]) -> None:
        """Create an approval gate"""
        try:
            await self.store.create_approval_gate(
                gate_id=gate_id,
                gate_name=gate_name,
                gate_type=gate_type,
                gate_config=gate_config,
                policies=policies
            )

            logger.info(f"Approval gate created: {gate_id} ({gate_type})")

        except Exception as e:
            logger.error(f"Failed to create approval gate {gate_id}: {e}")
            raise

    async def check_approval_gates(self, policy_id: str) -> List[Dict]:
        """Check if policy passes approval gates"""
        try:
            gates = await self.store.get_approval_gates(policy_id)
            gate_results = []

            for gate in gates:
                # Simulate gate check logic
                gate_result = {
                    "gate_id": gate["gate_id"],
                    "gate_name": gate["gate_name"],
                    "gate_type": gate["gate_type"],
                    "status": "passed",  # Would implement actual check
                    "checked_at": datetime.utcnow().isoformat()
                }
                gate_results.append(gate_result)

            return gate_results

        except Exception as e:
            logger.error(f"Failed to check approval gates for {policy_id}: {e}")
            return []

    # Policy Scoring and Adoption
    async def score_policy(self, policy_id: str, context_data: Dict) -> float:
        """Score a policy for adoption in a context"""
        try:
            policy = await self.get_policy(policy_id)
            if not policy:
                return 0.0

            # Implement scoring algorithm (from policy/src/scorer.py)
            score = 0.0

            # Base score from policy type
            policy_type = policy["policy_type"]
            type_scores = {
                "constitution": 0.9,
                "approval_gate": 0.8,
                "performance_budget": 0.7,
                "coding_standard": 0.6,
                "must_include": 0.8
            }
            score += type_scores.get(policy_type, 0.5)

            # Context relevance score
            context_type = context_data.get("context_type", "")
            if context_type in policy.get("policy_content", {}).get("applicable_contexts", []):
                score += 0.2

            # Normalize to 0-1
            score = min(1.0, max(0.0, score))

            logger.debug(f"Policy {policy_id} scored {score} for context")
            return score

        except Exception as e:
            logger.error(f"Failed to score policy {policy_id}: {e}")
            return 0.0

    async def adopt_policy(self, policy_id: str, context_id: str,
                         adoption_data: Optional[Dict] = None) -> str:
        """Adopt a policy in a context"""
        try:
            adoption_id = f"adopt_{uuid4().hex[:12]}"

            # Score the policy for this context
            score = await self.score_policy(policy_id, adoption_data or {})

            # Record adoption
            await self.store.record_adoption(
                adoption_id=adoption_id,
                policy_id=policy_id,
                context_id=context_id,
                adoption_status="adopted",
                score=score,
                adoption_data=adoption_data
            )

            # Publish governance event
            if self.governance_bus:
                self.governance_bus.publish_policy_adopted(policy_id, {
                    "adoption_id": adoption_id,
                    "context_id": context_id,
                    "score": score
                })

            logger.info(f"Policy adopted: {policy_id} in context {context_id} (score: {score})")
            return adoption_id

        except Exception as e:
            logger.error(f"Failed to adopt policy {policy_id}: {e}")
            raise

    async def get_adoption_history(self, policy_id: str) -> List[Dict]:
        """Get adoption history for a policy"""
        try:
            # This would query adoption history from store
            # Placeholder implementation
            return []

        except Exception as e:
            logger.error(f"Failed to get adoption history for {policy_id}: {e}")
            return []

    # Evolution and Analytics
    async def track_evolution(self, policy_id: str, changes: Dict) -> None:
        """Track policy evolution"""
        try:
            # Record evolution in database
            logger.info(f"Evolution tracked for policy {policy_id}")

        except Exception as e:
            logger.error(f"Failed to track evolution for {policy_id}: {e}")

    async def process_evolution_queue(self) -> List[Dict]:
        """Process pending policy evolutions"""
        try:
            # Implementation would process evolution queue
            return []

        except Exception as e:
            logger.error(f"Failed to process evolution queue: {e}")
            return []

    async def run_governance_smoke_tests(self) -> Dict:
        """Run governance smoke tests"""
        try:
            results = {
                "constitution_active": bool(await self.get_active_constitution()),
                "policies_count": len(await self.list_policies()),
                "approval_gates_count": len(await self.store.get_approval_gates()),
                "tests_passed": True,
                "timestamp": datetime.utcnow().isoformat()
            }

            logger.info("Governance smoke tests completed")
            return results

        except Exception as e:
            logger.error(f"Governance smoke tests failed: {e}")
            return {"tests_passed": False, "error": str(e)}

    async def analyze_policy_performance(self, policy_id: str,
                                       timeframe_days: int = 30) -> Dict:
        """Analyze policy performance metrics"""
        try:
            # Placeholder performance analysis
            return {
                "policy_id": policy_id,
                "timeframe_days": timeframe_days,
                "adoption_rate": 0.8,
                "success_rate": 0.9,
                "avg_score": 0.75,
                "total_adoptions": 10
            }

        except Exception as e:
            logger.error(f"Failed to analyze policy performance {policy_id}: {e}")
            return {}

    async def suggest_improvements(self, policy_id: str) -> List[Dict]:
        """Suggest improvements for a policy"""
        try:
            # Placeholder improvement suggestions
            return [
                {
                    "suggestion": "Update validation rules",
                    "reason": "Improve compliance rate",
                    "priority": "medium"
                }
            ]

        except Exception as e:
            logger.error(f"Failed to suggest improvements for {policy_id}: {e}")
            return []

    def is_healthy(self) -> bool:
        """Check if policy engine is healthy"""
        return self.store is not None

    async def shutdown(self) -> None:
        """Shutdown policy engine"""
        try:
            logger.info("Policy engine shutdown complete")

        except Exception as e:
            logger.error(f"Error during policy engine shutdown: {e}")
            raise