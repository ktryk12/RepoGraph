"""
Truth Service - Business Logic Layer

Implements truth management business logic on top of the database layer.
Handles validation, workflow, and business rules for truth and proposals.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from uuid import uuid4

logger = logging.getLogger(__name__)

class TruthService:
    """Business logic for truth management."""

    def __init__(self, database):
        self.database = database

    # Fact management

    async def create_fact(self, fact_data: Dict[str, Any]) -> str:
        """Create a new truth fact with validation."""
        # Validate required fields
        if not fact_data.get("fact_content"):
            raise ValueError("fact_content is required")

        if not fact_data.get("created_by"):
            raise ValueError("created_by is required")

        # Generate ID if not provided
        if "fact_id" not in fact_data:
            fact_data["fact_id"] = str(uuid4())

        # Set defaults
        fact_data.setdefault("fact_type", "assertion")
        fact_data.setdefault("confidence", 1.0)
        fact_data.setdefault("status", "active")
        fact_data.setdefault("version", 1)
        fact_data.setdefault("source_type", "system")
        fact_data.setdefault("created_at", datetime.now().isoformat())

        # Validate confidence range
        confidence = fact_data.get("confidence", 1.0)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")

        # Check for duplicates
        existing_facts = await self.search_facts(fact_data["fact_content"])
        for existing in existing_facts:
            if existing["fact_content"] == fact_data["fact_content"]:
                logger.warning(f"Similar fact already exists: {existing['fact_id']}")
                # Could implement similarity checking here

        # Create fact
        fact_id = await self.database.create_fact(fact_data)

        # Create relationships if specified
        relationships = fact_data.get("relationships", [])
        for rel in relationships:
            await self.create_fact_relationship(
                fact_id,
                rel["target_fact_id"],
                rel["relationship_type"],
                rel.get("strength", 1.0)
            )

        logger.info(f"Created fact {fact_id} by {fact_data['created_by']}")
        return fact_id

    async def get_fact(self, fact_id: str) -> Optional[Dict[str, Any]]:
        """Get a fact by ID."""
        fact = await self.database.get_fact(fact_id)
        if fact:
            # Enrich with relationships
            relationships = await self.database.get_fact_relationships(fact_id)
            fact["relationships"] = relationships

        return fact

    async def list_facts(self, limit: int = 100, offset: int = 0,
                        status: str = "active") -> List[Dict[str, Any]]:
        """List facts with pagination."""
        return await self.database.list_facts(limit, offset, status)

    async def update_fact(self, fact_id: str, updates: Dict[str, Any],
                         updated_by: str) -> bool:
        """Update a fact with versioning."""
        # Get current fact
        current_fact = await self.database.get_fact(fact_id)
        if not current_fact:
            raise ValueError(f"Fact {fact_id} not found")

        # Increment version for significant changes
        significant_changes = ["fact_content", "fact_type", "confidence", "status"]
        if any(field in updates for field in significant_changes):
            updates["version"] = current_fact["version"] + 1

        updates["updated_at"] = datetime.now().isoformat()

        # Update fact
        success = await self.database.update_fact(fact_id, updates)

        if success:
            logger.info(f"Updated fact {fact_id} by {updated_by}")

        return success

    async def deprecate_fact(self, fact_id: str, reason: str, deprecated_by: str) -> bool:
        """Deprecate a fact."""
        success = await self.database.deprecate_fact(fact_id, reason)

        if success:
            logger.info(f"Deprecated fact {fact_id} by {deprecated_by}: {reason}")

        return success

    async def supersede_fact(self, old_fact_id: str, new_fact_data: Dict[str, Any]) -> str:
        """Supersede an old fact with a new one."""
        # Create new fact
        new_fact_data["supersedes_fact_id"] = old_fact_id
        new_fact_id = await self.create_fact(new_fact_data)

        # Update old fact status
        await self.update_fact(
            old_fact_id,
            {"status": "superseded"},
            new_fact_data["created_by"]
        )

        logger.info(f"Superseded fact {old_fact_id} with {new_fact_id}")
        return new_fact_id

    # Proposal management

    async def create_proposal(self, proposal_data: Dict[str, Any]) -> str:
        """Create a new proposal with validation."""
        # Validate required fields
        if not proposal_data.get("proposed_fact"):
            raise ValueError("proposed_fact is required")

        if not proposal_data.get("submitted_by"):
            raise ValueError("submitted_by is required")

        # Generate ID if not provided
        if "proposal_id" not in proposal_data:
            proposal_data["proposal_id"] = str(uuid4())

        # Set defaults
        proposal_data.setdefault("proposal_type", "new_fact")
        proposal_data.setdefault("status", "pending")
        proposal_data.setdefault("priority", "normal")
        proposal_data.setdefault("submitted_at", datetime.now().isoformat())

        # Set expiration if not provided (default 30 days)
        if not proposal_data.get("expires_at"):
            expires_at = datetime.now() + timedelta(days=30)
            proposal_data["expires_at"] = expires_at.isoformat()

        # Validate proposal type
        valid_types = ["new_fact", "fact_update", "fact_deprecation", "fact_correction", "relationship_addition"]
        if proposal_data["proposal_type"] not in valid_types:
            raise ValueError(f"proposal_type must be one of: {valid_types}")

        # For updates/corrections, target_fact_id is required
        if proposal_data["proposal_type"] in ["fact_update", "fact_deprecation", "fact_correction"]:
            if not proposal_data.get("target_fact_id"):
                raise ValueError(f"target_fact_id required for {proposal_data['proposal_type']}")

            # Validate target fact exists
            target_fact = await self.database.get_fact(proposal_data["target_fact_id"])
            if not target_fact:
                raise ValueError(f"Target fact {proposal_data['target_fact_id']} not found")

        # Create proposal
        proposal_id = await self.database.create_proposal(proposal_data)

        logger.info(f"Created proposal {proposal_id} by {proposal_data['submitted_by']}")
        return proposal_id

    async def get_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """Get a proposal by ID."""
        return await self.database.get_proposal(proposal_id)

    async def list_proposals(self, status: str = "pending", limit: int = 100,
                           offset: int = 0) -> List[Dict[str, Any]]:
        """List proposals with pagination."""
        return await self.database.list_proposals(status, limit, offset)

    async def review_proposal(self, proposal_id: str, decision: str,
                            reviewer_id: str, review_notes: str = None) -> Dict[str, Any]:
        """Review a proposal (approve or reject)."""
        if decision not in ["approved", "rejected"]:
            raise ValueError("decision must be 'approved' or 'rejected'")

        # Get proposal
        proposal = await self.database.get_proposal(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal {proposal_id} not found")

        if proposal["status"] != "pending":
            raise ValueError(f"Proposal {proposal_id} is not pending (status: {proposal['status']})")

        # Update proposal status
        await self.database.update_proposal_status(
            proposal_id, decision, reviewer_id, review_notes
        )

        result = {
            "proposal_id": proposal_id,
            "decision": decision,
            "reviewer_id": reviewer_id,
            "completed_at": datetime.now().isoformat()
        }

        # If approved, execute the proposal
        if decision == "approved":
            execution_result = await self._execute_proposal(proposal)
            result.update(execution_result)

        logger.info(f"Reviewed proposal {proposal_id}: {decision} by {reviewer_id}")
        return result

    async def _execute_proposal(self, proposal: Dict[str, Any]) -> Dict[str, Any]:
        """Execute an approved proposal."""
        proposal_type = proposal["proposal_type"]
        result = {"executed": True, "actions": []}

        try:
            if proposal_type == "new_fact":
                # Create new fact
                fact_data = {
                    "fact_content": proposal["proposed_fact"],
                    "fact_type": "assertion",  # Default type
                    "created_by": f"proposal:{proposal['proposal_id']}",
                    "source_id": proposal["submitted_by"],
                    "source_type": "proposal"
                }

                fact_id = await self.create_fact(fact_data)
                result["actions"].append({"type": "fact_created", "fact_id": fact_id})

            elif proposal_type == "fact_update":
                # Update existing fact
                updates = {
                    "fact_content": proposal["proposed_fact"],
                    "updated_at": datetime.now().isoformat()
                }

                success = await self.update_fact(
                    proposal["target_fact_id"],
                    updates,
                    f"proposal:{proposal['proposal_id']}"
                )

                if success:
                    result["actions"].append({
                        "type": "fact_updated",
                        "fact_id": proposal["target_fact_id"]
                    })

            elif proposal_type == "fact_deprecation":
                # Deprecate fact
                success = await self.deprecate_fact(
                    proposal["target_fact_id"],
                    proposal.get("justification", "Deprecated via proposal"),
                    f"proposal:{proposal['proposal_id']}"
                )

                if success:
                    result["actions"].append({
                        "type": "fact_deprecated",
                        "fact_id": proposal["target_fact_id"]
                    })

            elif proposal_type == "fact_correction":
                # Supersede with corrected version
                corrected_fact_data = {
                    "fact_content": proposal["proposed_fact"],
                    "created_by": f"proposal:{proposal['proposal_id']}",
                    "source_id": proposal["submitted_by"],
                    "source_type": "correction"
                }

                new_fact_id = await self.supersede_fact(
                    proposal["target_fact_id"],
                    corrected_fact_data
                )

                result["actions"].append({
                    "type": "fact_superseded",
                    "old_fact_id": proposal["target_fact_id"],
                    "new_fact_id": new_fact_id
                })

        except Exception as e:
            logger.error(f"Failed to execute proposal {proposal['proposal_id']}: {e}")
            result["executed"] = False
            result["error"] = str(e)

        return result

    # Relationship management

    async def create_fact_relationship(self, source_fact_id: str, target_fact_id: str,
                                     relationship_type: str, strength: float = 1.0,
                                     description: str = None) -> str:
        """Create a relationship between facts."""
        # Validate facts exist
        source_fact = await self.database.get_fact(source_fact_id)
        target_fact = await self.database.get_fact(target_fact_id)

        if not source_fact:
            raise ValueError(f"Source fact {source_fact_id} not found")
        if not target_fact:
            raise ValueError(f"Target fact {target_fact_id} not found")

        # Validate relationship type
        valid_types = ["depends_on", "contradicts", "supports", "derives_from", "similar_to"]
        if relationship_type not in valid_types:
            raise ValueError(f"relationship_type must be one of: {valid_types}")

        # Validate strength
        if not 0.0 <= strength <= 1.0:
            raise ValueError("strength must be between 0.0 and 1.0")

        return await self.database.create_relationship(
            source_fact_id, target_fact_id, relationship_type, strength, description
        )

    # Search and query

    async def search_facts(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search facts by content."""
        return await self.database.search_facts(query, limit)

    async def get_facts_by_type(self, fact_type: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get facts by type."""
        return await self.database.get_facts_by_type(fact_type, limit)

    async def get_related_facts(self, fact_id: str, relationship_type: str = None) -> List[Dict[str, Any]]:
        """Get facts related to a given fact."""
        relationships = await self.database.get_fact_relationships(fact_id)

        if relationship_type:
            relationships = [r for r in relationships if r["relationship_type"] == relationship_type]

        related_facts = []
        for rel in relationships:
            # Get the other fact in the relationship
            other_fact_id = rel["target_fact_id"] if rel["source_fact_id"] == fact_id else rel["source_fact_id"]
            other_fact = await self.database.get_fact(other_fact_id)

            if other_fact:
                other_fact["relationship"] = rel
                related_facts.append(other_fact)

        return related_facts

    # Maintenance and cleanup

    async def cleanup_expired_proposals(self) -> int:
        """Clean up expired proposals."""
        current_time = datetime.now().isoformat()

        # Get expired proposals
        expired_proposals = await self.database.list_proposals(status="pending", limit=1000)
        expired_proposals = [
            p for p in expired_proposals
            if p.get("expires_at") and p["expires_at"] < current_time
        ]

        count = 0
        for proposal in expired_proposals:
            await self.database.update_proposal_status(
                proposal["proposal_id"],
                "expired",
                "system",
                "Expired due to timeout"
            )
            count += 1

        if count > 0:
            logger.info(f"Cleaned up {count} expired proposals")

        return count

    async def get_metrics(self) -> Dict[str, Any]:
        """Get service metrics."""
        stats = await self.database.get_stats()

        # Add computed metrics
        total_facts = sum(stats.get("facts", {}).values())
        total_proposals = sum(stats.get("proposals", {}).values())

        metrics = {
            "truth_service": {
                "total_facts": total_facts,
                "active_facts": stats.get("facts", {}).get("active", 0),
                "deprecated_facts": stats.get("facts", {}).get("deprecated", 0),
                "total_proposals": total_proposals,
                "pending_proposals": stats.get("proposals", {}).get("pending", 0),
                "approved_proposals": stats.get("proposals", {}).get("approved", 0),
                "rejected_proposals": stats.get("proposals", {}).get("rejected", 0),
                "total_relationships": stats.get("relationships", 0)
            },
            "timestamp": datetime.now().isoformat()
        }

        return metrics