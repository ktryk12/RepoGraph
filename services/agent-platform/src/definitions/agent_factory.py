"""
Agent Factory Module

Consolidated from services/agents/
Provides agent definition management and agent creation functionality
with PostgreSQL persistence.
"""

import logging
from typing import Dict, List, Optional, Any, Type
from datetime import datetime

logger = logging.getLogger(__name__)


class AgentFactory:
    """
    Agent factory and definition management service

    Consolidated functionality from agents service:
    - Agent definition storage and retrieval
    - Agent type registration and management
    - Agent instantiation and configuration
    - Agent metadata and capabilities management
    """

    def __init__(self, store):
        self.store = store
        self.agent_types: Dict[str, Type] = {}
        self._registered_agent_classes = {}

    async def initialize(self) -> None:
        """Initialize the factory module"""
        try:
            # Register built-in agent types
            await self._register_built_in_agents()

            logger.info("Agent factory module initialized")

        except Exception as e:
            logger.error(f"Failed to initialize agent factory: {e}")
            raise

    async def _register_built_in_agents(self) -> None:
        """Register built-in agent types from consolidated agents service"""

        # Register agent types from agents/src/
        built_in_agents = [
            {
                "agent_type": "architect",
                "agent_class": "ArchitectAgent",
                "capabilities": ["code_generation", "architecture_design", "planning"],
                "description": "Software architecture and code generation agent"
            },
            {
                "agent_type": "supervisor",
                "agent_class": "SupervisorAgent",
                "capabilities": ["task_orchestration", "agent_management", "monitoring"],
                "description": "Agent orchestration and supervision"
            },
            {
                "agent_type": "librarian",
                "agent_class": "LibrarianAgent",
                "capabilities": ["knowledge_management", "documentation", "search"],
                "description": "Knowledge management and documentation agent"
            },
            {
                "agent_type": "evaluator",
                "agent_class": "EvaluatorAgent",
                "capabilities": ["testing", "validation", "quality_assurance"],
                "description": "Code and task evaluation agent"
            },
            {
                "agent_type": "repair",
                "agent_class": "RepairAgent",
                "capabilities": ["error_diagnosis", "code_repair", "debugging"],
                "description": "Error repair and debugging agent"
            },
            {
                "agent_type": "translator",
                "agent_class": "TranslatorAgent",
                "capabilities": ["language_translation", "code_translation", "format_conversion"],
                "description": "Language and format translation agent"
            },
            {
                "agent_type": "validation",
                "agent_class": "ValidationAgent",
                "capabilities": ["input_validation", "constraint_checking", "verification"],
                "description": "Input validation and constraint verification agent"
            },
            {
                "agent_type": "voice_io",
                "agent_class": "VoiceIOAgent",
                "capabilities": ["voice_input", "voice_output", "speech_processing"],
                "description": "Voice input/output processing agent"
            },
            {
                "agent_type": "requirements",
                "agent_class": "RequirementsAgent",
                "capabilities": ["requirements_analysis", "specification_generation", "compliance_checking"],
                "description": "Requirements analysis and specification agent"
            },
            {
                "agent_type": "failure_logger",
                "agent_class": "FailureLoggerAgent",
                "capabilities": ["error_logging", "failure_analysis", "telemetry"],
                "description": "Failure logging and analysis agent"
            }
        ]

        for agent_spec in built_in_agents:
            self._registered_agent_classes[agent_spec["agent_type"]] = agent_spec

        logger.info(f"Registered {len(built_in_agents)} built-in agent types")

    # Agent Definition Management
    async def create_agent_definition(self, agent_id: str, agent_name: str,
                                    agent_type: str, agent_spec: Dict,
                                    metadata: Optional[Dict] = None) -> None:
        """
        Create a new agent definition

        Args:
            agent_id: Unique identifier for the agent
            agent_name: Human-readable name
            agent_type: Type of agent (must be registered)
            agent_spec: Agent specification and configuration
            metadata: Additional metadata
        """
        try:
            # Validate agent type
            if agent_type not in self._registered_agent_classes:
                raise ValueError(f"Unknown agent type: {agent_type}")

            # Enhance agent_spec with type information
            enhanced_spec = {
                **agent_spec,
                "agent_class": self._registered_agent_classes[agent_type]["agent_class"],
                "capabilities": self._registered_agent_classes[agent_type]["capabilities"],
                "created_at": datetime.utcnow().isoformat()
            }

            # Store in database
            await self.store.create_agent_definition(
                agent_id=agent_id,
                agent_name=agent_name,
                agent_type=agent_type,
                agent_spec=enhanced_spec,
                metadata=metadata
            )

            logger.info(f"Created agent definition: {agent_id} ({agent_type})")

        except Exception as e:
            logger.error(f"Failed to create agent definition {agent_id}: {e}")
            raise

    async def get_agent_definition(self, agent_id: str) -> Optional[Dict]:
        """Get agent definition by ID"""
        try:
            return await self.store.get_agent_definition(agent_id)

        except Exception as e:
            logger.error(f"Failed to get agent definition {agent_id}: {e}")
            return None

    async def update_agent_definition(self, agent_id: str, updates: Dict) -> None:
        """Update an existing agent definition"""
        try:
            # Get existing definition
            existing = await self.get_agent_definition(agent_id)
            if not existing:
                raise ValueError(f"Agent definition not found: {agent_id}")

            # Merge updates
            updated_spec = {**existing["agent_spec"], **updates.get("agent_spec", {})}
            updated_metadata = {**existing["metadata"], **updates.get("metadata", {})}

            # Update in database
            await self.store.create_agent_definition(
                agent_id=agent_id,
                agent_name=updates.get("agent_name", existing["agent_name"]),
                agent_type=updates.get("agent_type", existing["agent_type"]),
                agent_spec=updated_spec,
                metadata=updated_metadata
            )

            logger.info(f"Updated agent definition: {agent_id}")

        except Exception as e:
            logger.error(f"Failed to update agent definition {agent_id}: {e}")
            raise

    async def list_available_agents(self, agent_type: Optional[str] = None) -> List[Dict]:
        """List available agent definitions"""
        try:
            if agent_type:
                return await self.store.list_agents_by_type(agent_type)
            else:
                # Get all agent types
                all_agents = []
                for registered_type in self._registered_agent_classes:
                    agents = await self.store.list_agents_by_type(registered_type)
                    all_agents.extend(agents)
                return all_agents

        except Exception as e:
            logger.error(f"Failed to list available agents: {e}")
            return []

    async def delete_agent_definition(self, agent_id: str) -> bool:
        """Delete an agent definition (soft delete by marking inactive)"""
        try:
            # Mark as inactive in metadata rather than hard delete
            agent = await self.get_agent_definition(agent_id)
            if not agent:
                return False

            metadata = agent.get("metadata", {})
            metadata["status"] = "inactive"
            metadata["deleted_at"] = datetime.utcnow().isoformat()

            await self.update_agent_definition(agent_id, {"metadata": metadata})

            logger.info(f"Marked agent definition as inactive: {agent_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete agent definition {agent_id}: {e}")
            return False

    # Agent Type Management
    def register_agent_type(self, agent_type: str, agent_spec: Dict) -> None:
        """Register a new agent type"""
        try:
            self._registered_agent_classes[agent_type] = agent_spec
            logger.info(f"Registered new agent type: {agent_type}")

        except Exception as e:
            logger.error(f"Failed to register agent type {agent_type}: {e}")
            raise

    def get_registered_agent_types(self) -> List[Dict]:
        """Get all registered agent types"""
        return list(self._registered_agent_classes.values())

    def get_agent_type_info(self, agent_type: str) -> Optional[Dict]:
        """Get information about a specific agent type"""
        return self._registered_agent_classes.get(agent_type)

    # Agent Template and Validation
    async def validate_agent_spec(self, agent_type: str, agent_spec: Dict) -> Dict:
        """
        Validate agent specification against type requirements

        Returns validation result with any errors or warnings
        """
        try:
            validation_result = {
                "valid": True,
                "errors": [],
                "warnings": []
            }

            # Check if agent type is registered
            if agent_type not in self._registered_agent_classes:
                validation_result["valid"] = False
                validation_result["errors"].append(f"Unknown agent type: {agent_type}")
                return validation_result

            type_info = self._registered_agent_classes[agent_type]

            # Basic validation checks
            required_fields = ["version", "config"]
            for field in required_fields:
                if field not in agent_spec:
                    validation_result["warnings"].append(f"Missing recommended field: {field}")

            # Type-specific validation could be added here
            logger.debug(f"Validated agent spec for type: {agent_type}")

            return validation_result

        except Exception as e:
            logger.error(f"Failed to validate agent spec: {e}")
            return {
                "valid": False,
                "errors": [str(e)],
                "warnings": []
            }

    # Factory Statistics
    async def get_factory_statistics(self) -> Dict:
        """Get factory statistics"""
        try:
            stats = {
                "registered_types": len(self._registered_agent_classes),
                "total_definitions": 0,
                "by_type": {}
            }

            # Count definitions by type
            for agent_type in self._registered_agent_classes:
                agents = await self.store.list_agents_by_type(agent_type)
                active_agents = [a for a in agents if a.get("metadata", {}).get("status") != "inactive"]

                stats["by_type"][agent_type] = len(active_agents)
                stats["total_definitions"] += len(active_agents)

            return stats

        except Exception as e:
            logger.error(f"Failed to get factory statistics: {e}")
            return {}

    def is_healthy(self) -> bool:
        """Check if factory module is healthy"""
        return self.store is not None

    async def shutdown(self) -> None:
        """Shutdown the factory module"""
        try:
            logger.info("Agent factory module shutdown complete")

        except Exception as e:
            logger.error(f"Error during factory shutdown: {e}")
            raise