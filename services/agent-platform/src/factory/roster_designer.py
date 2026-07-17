"""
Phase 7 Roster Designer

Handles agent roster generation based on classified intent and scope analysis.
Stage 1: Placeholder functionality for shadow deployment.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from .state_machine import (
    RosterConfig, AgentSpec, IntentClassification,
    ComplexityLevel, complexity_to_max_agents
)

logger = logging.getLogger(__name__)


class RosterDesigner:
    """
    Roster designer for generating agent teams based on task intent

    Stage 1: Placeholder functionality
    Stage 2+: Full roster generation with template library
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.agent_templates = {}
        self.roster_statistics = {
            "total_rosters_designed": 0,
            "by_complexity": {level.value: 0 for level in ComplexityLevel},
            "by_profile": {},
            "average_agents_per_roster": 0
        }

        logger.info("Roster Designer initialized for Stage 1")

    async def initialize(self) -> None:
        """Initialize roster designer"""
        try:
            # Load agent templates for roster generation
            await self._load_agent_templates()

            logger.info("Roster Designer initialization complete")

        except Exception as e:
            logger.error(f"Failed to initialize roster designer: {e}")
            raise

    async def _load_agent_templates(self) -> None:
        """Load agent templates for different use cases"""

        # Codebase analysis roster template
        self.agent_templates["codebase_analysis"] = {
            "max_agents": 5,
            "coordination": "linear",
            "agents": [
                {
                    "agent_type": "mapper",
                    "agent_name": "codebase_mapper",
                    "tools": ["filesystem_reader", "tree_sitter", "git_analyzer"],
                    "model": "claude-sonnet-4-6",
                    "config": {"output_format": "structured_map"}
                },
                {
                    "agent_type": "retriever",
                    "agent_name": "code_retriever",
                    "tools": ["vector_search", "semantic_indexer"],
                    "model": "claude-sonnet-4-6",
                    "config": {"embedding_model": "text-embedding-3-large"},
                    "dependencies": ["codebase_mapper"]
                },
                {
                    "agent_type": "analyst",
                    "agent_name": "architecture_analyst",
                    "tools": ["dependency_analyzer", "pattern_detector"],
                    "model": "claude-sonnet-4-6",
                    "config": {"analysis_focus": "architecture"},
                    "dependencies": ["code_retriever"]
                },
                {
                    "agent_type": "analyst",
                    "agent_name": "tech_debt_analyst",
                    "tools": ["code_quality_scanner", "churn_analyzer"],
                    "model": "claude-sonnet-4-6",
                    "config": {"analysis_focus": "tech_debt"},
                    "dependencies": ["code_retriever"]
                },
                {
                    "agent_type": "analyst",
                    "agent_name": "security_analyst",
                    "tools": ["security_scanner", "vulnerability_detector"],
                    "model": "claude-sonnet-4-6",
                    "config": {"analysis_focus": "security"},
                    "dependencies": ["code_retriever"]
                },
                {
                    "agent_type": "reporter",
                    "agent_name": "analysis_reporter",
                    "tools": ["markdown_generator", "chart_generator"],
                    "model": "claude-sonnet-4-6",
                    "config": {"output_format": "comprehensive_report"},
                    "dependencies": ["architecture_analyst", "tech_debt_analyst", "security_analyst"]
                }
            ]
        }

        # Research task roster template
        self.agent_templates["research"] = {
            "max_agents": 3,
            "coordination": "linear",
            "agents": [
                {
                    "agent_type": "researcher",
                    "agent_name": "web_researcher",
                    "tools": ["web_browser", "search_engine", "document_parser"],
                    "model": "claude-sonnet-4-6",
                    "config": {"search_depth": "comprehensive"}
                },
                {
                    "agent_type": "analyzer",
                    "agent_name": "content_analyzer",
                    "tools": ["text_analyzer", "fact_checker"],
                    "model": "claude-sonnet-4-6",
                    "config": {"analysis_type": "content_quality"},
                    "dependencies": ["web_researcher"]
                },
                {
                    "agent_type": "synthesizer",
                    "agent_name": "research_synthesizer",
                    "tools": ["summarizer", "citation_manager"],
                    "model": "claude-sonnet-4-6",
                    "config": {"output_format": "research_report"},
                    "dependencies": ["content_analyzer"]
                }
            ]
        }

        # Simple editing roster template
        self.agent_templates["code_editing"] = {
            "max_agents": 2,
            "coordination": "linear",
            "agents": [
                {
                    "agent_type": "code_editor",
                    "agent_name": "primary_editor",
                    "tools": ["file_editor", "syntax_checker", "formatter"],
                    "model": "claude-sonnet-4-6",
                    "config": {"edit_scope": "focused"}
                },
                {
                    "agent_type": "validator",
                    "agent_name": "edit_validator",
                    "tools": ["test_runner", "linter", "type_checker"],
                    "model": "claude-sonnet-4-6",
                    "config": {"validation_level": "comprehensive"},
                    "dependencies": ["primary_editor"]
                }
            ]
        }

        logger.info(f"Loaded {len(self.agent_templates)} agent roster templates")

    async def design_roster(self, intent: IntentClassification, scope: str, context: Dict[str, Any]) -> RosterConfig:
        """
        Design agent roster based on intent classification and scope

        Stage 1: Return placeholder roster
        Stage 2+: Full roster generation with template matching
        """
        try:
            # Update statistics
            self.roster_statistics["total_rosters_designed"] += 1
            self.roster_statistics["by_complexity"][intent.complexity.value] += 1
            profile_count = self.roster_statistics["by_profile"].get(intent.profile, 0)
            self.roster_statistics["by_profile"][intent.profile] = profile_count + 1

            # For Stage 1, return a simple placeholder roster
            max_agents = complexity_to_max_agents(intent.complexity)

            # Select template based on profile and detected capabilities
            template_name = self._select_template(intent, context)
            template = self.agent_templates.get(template_name, self.agent_templates["codebase_analysis"])

            # Create roster config from template
            roster = RosterConfig(
                max_agents=min(max_agents, template["max_agents"]),
                max_depth=1,  # Stage 1 conservative
                coordination=template.get("coordination", "linear"),
                agents=[
                    AgentSpec(
                        agent_type=agent["agent_type"],
                        agent_name=agent["agent_name"],
                        tools=agent["tools"],
                        model=agent["model"],
                        config=agent["config"],
                        dependencies=agent.get("dependencies")
                    )
                    for agent in template["agents"][:max_agents]
                ],
                execution_order=self._determine_execution_order(template["agents"][:max_agents])
            )

            logger.info(f"Designed roster with {len(roster.agents)} agents for {intent.profile} profile")
            return roster

        except Exception as e:
            logger.error(f"Roster design failed: {e}")

            # Safe fallback roster
            return RosterConfig(
                max_agents=1,
                max_depth=1,
                coordination="linear",
                agents=[
                    AgentSpec(
                        agent_type="assistant",
                        agent_name="general_assistant",
                        tools=["text_processor"],
                        model="claude-sonnet-4-6",
                        config={"mode": "safe_default"}
                    )
                ],
                execution_order=["general_assistant"]
            )

    def _select_template(self, intent: IntentClassification, context: Dict[str, Any]) -> str:
        """Select appropriate roster template based on intent"""

        # Map profiles to templates
        profile_template_map = {
            "readonly": "codebase_analysis",
            "research": "research",
            "editor": "code_editing",
            "builder": "codebase_analysis",  # More complex analysis for building
            "operator": "research",  # Investigation-focused
            "admin_assisted": "codebase_analysis"  # Comprehensive analysis
        }

        # Check detected capabilities for more specific matching
        if intent.detected_capabilities:
            if "read_code" in intent.detected_capabilities and "write_code" not in intent.detected_capabilities:
                return "codebase_analysis"
            elif "write_code" in intent.detected_capabilities:
                return "code_editing"
            elif "browse_web" in intent.detected_capabilities:
                return "research"

        # Use profile-based default
        return profile_template_map.get(intent.profile, "codebase_analysis")

    def _determine_execution_order(self, agents: List[Dict[str, Any]]) -> List[str]:
        """Determine execution order based on agent dependencies"""

        # Simple topological sort for dependencies
        agent_names = [agent["agent_name"] for agent in agents]
        dependencies = {
            agent["agent_name"]: agent.get("dependencies", [])
            for agent in agents
        }

        execution_order = []
        remaining = set(agent_names)

        while remaining:
            # Find agents with no unresolved dependencies
            ready = [
                name for name in remaining
                if all(dep in execution_order for dep in dependencies[name])
            ]

            if not ready:
                # Circular dependency or other issue, add remaining in order
                execution_order.extend(sorted(remaining))
                break

            # Add ready agents to execution order
            for agent in ready:
                execution_order.append(agent)
                remaining.remove(agent)

        return execution_order

    async def validate_roster(self, roster: RosterConfig, policy_constraints: Dict[str, Any]) -> Dict[str, Any]:
        """Validate roster against policy constraints"""
        try:
            validation_result = {
                "valid": True,
                "errors": [],
                "warnings": []
            }

            # Check max_agents constraint
            max_allowed = policy_constraints.get("max_agents", 10)
            if roster.max_agents > max_allowed:
                validation_result["valid"] = False
                validation_result["errors"].append(f"Roster exceeds max_agents: {roster.max_agents} > {max_allowed}")

            # Check max_depth constraint
            max_depth_allowed = policy_constraints.get("max_depth", 5)
            if roster.max_depth > max_depth_allowed:
                validation_result["valid"] = False
                validation_result["errors"].append(f"Roster exceeds max_depth: {roster.max_depth} > {max_depth_allowed}")

            # Validate agent tools against allowed capabilities
            allowed_capabilities = policy_constraints.get("allowed_capabilities", [])
            if allowed_capabilities:
                for agent in roster.agents:
                    for tool in agent.tools:
                        if tool not in allowed_capabilities:
                            validation_result["warnings"].append(f"Agent {agent.agent_name} uses potentially restricted tool: {tool}")

            # Check for execution order consistency
            if roster.execution_order:
                roster_agent_names = {agent.agent_name for agent in roster.agents}
                order_names = set(roster.execution_order)

                if roster_agent_names != order_names:
                    validation_result["valid"] = False
                    validation_result["errors"].append("Execution order doesn't match roster agents")

            return validation_result

        except Exception as e:
            return {
                "valid": False,
                "errors": [f"Validation error: {str(e)}"],
                "warnings": []
            }

    def get_available_templates(self) -> List[str]:
        """Get list of available roster templates"""
        return list(self.agent_templates.keys())

    def get_template_info(self, template_name: str) -> Optional[Dict[str, Any]]:
        """Get information about a specific template"""
        return self.agent_templates.get(template_name)

    async def get_statistics(self) -> Dict[str, Any]:
        """Get roster designer statistics"""
        total_designed = self.roster_statistics["total_rosters_designed"]

        if total_designed > 0:
            # Calculate average agents per roster
            total_agents = sum(
                count * complexity_to_max_agents(ComplexityLevel(complexity))
                for complexity, count in self.roster_statistics["by_complexity"].items()
            )
            avg_agents = total_agents / total_designed
        else:
            avg_agents = 0

        return {
            "total_rosters_designed": total_designed,
            "complexity_distribution": self.roster_statistics["by_complexity"],
            "profile_distribution": self.roster_statistics["by_profile"],
            "average_agents_per_roster": round(avg_agents, 2),
            "available_templates": list(self.agent_templates.keys()),
            "stage": "stage1_shadow"
        }

    async def shutdown(self) -> None:
        """Shutdown roster designer"""
        try:
            logger.info("Roster Designer shutdown complete")
        except Exception as e:
            logger.error(f"Error during roster designer shutdown: {e}")
            raise