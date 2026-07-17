"""
Consolidated Tool Platform Service

Integrates functionality from:
- tools/ (Tool definitions and implementations)
- tool-runtime/ (Tool execution infrastructure)
- skill-runtime/ (Skill execution and management)

Provides unified tool and skill platform with PostgreSQL persistence.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from uuid import uuid4

from .postgresql_tool_store import PostgreSQLToolStore

# Import consolidated modules
from .tools.tool_manager import ToolManager
from .runtime.tool_runtime import ToolRuntime
from .skills.skill_manager import SkillManager
from .infrastructure.tool_event_bus import ToolEventBus

# Import skill runtime components
from .skills.registry.skill_registry import SkillRegistry
from .skills.loader.skill_loader import SkillLoader
from .skills.executor.expert_client import ExpertClient
from .skills.validator.skill_validator import SkillValidator
from .skills.context.context_builder import ContextBuilder

logger = logging.getLogger(__name__)


class ToolPlatformService:
    """
    Consolidated tool platform service

    Provides unified interface for:
    - Tool definitions and registry (from tools/)
    - Tool execution and runtime (from tool-runtime/)
    - Skill definitions and execution (from skill-runtime/)
    - Performance monitoring and metrics
    - Event-driven tool and skill coordination
    """

    def __init__(self, database_url: str, kafka_servers: str = "kafka:9092"):
        self.database_url = database_url
        self.kafka_servers = kafka_servers

        # Core components
        self.store: Optional[PostgreSQLToolStore] = None
        self.tool_manager: Optional[ToolManager] = None
        self.tool_runtime: Optional[ToolRuntime] = None
        self.skill_manager: Optional[SkillManager] = None
        self.event_bus: Optional[ToolEventBus] = None

    async def initialize(self) -> None:
        """Initialize the tool platform service"""
        try:
            # Initialize PostgreSQL store
            self.store = await PostgreSQLToolStore.create(self.database_url)
            logger.info("Tool platform store initialized")

            # Initialize event bus
            self.event_bus = ToolEventBus(
                kafka_servers=self.kafka_servers,
                group_id="tool-platform"
            )
            await self.event_bus.initialize()

            # Initialize consolidated modules
            self.tool_manager = ToolManager(self.store, self.event_bus)
            self.tool_runtime = ToolRuntime(self.store, self.event_bus)
            self.skill_manager = SkillManager(self.store, self.event_bus, self.tool_runtime)

            # Initialize all modules
            await asyncio.gather(
                self.tool_manager.initialize(),
                self.tool_runtime.initialize(),
                self.skill_manager.initialize(),
            )

            # Setup event handlers
            await self._setup_event_handlers()

            # Start event consumer
            if self.event_bus:
                self.event_bus.start_consumer()

            logger.info("Tool platform service initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize tool platform service: {e}")
            raise

    async def _setup_event_handlers(self) -> None:
        """Setup tool platform event handlers"""
        if not self.event_bus:
            return

        # Tool lifecycle events
        self.event_bus.register_handler("tool_registered", self._handle_tool_registered)
        self.event_bus.register_handler("tool_executed", self._handle_tool_executed)

        # Skill lifecycle events
        self.event_bus.register_handler("skill_registered", self._handle_skill_registered)
        self.event_bus.register_handler("skill_executed", self._handle_skill_executed)
        self.event_bus.register_handler("skill_feedback", self._handle_skill_feedback)

        # Runtime events
        self.event_bus.register_handler("runtime_error", self._handle_runtime_error)

        logger.info("Tool platform event handlers registered")

    # Event Handlers
    async def _handle_tool_registered(self, payload: Dict) -> None:
        """Handle tool registered event"""
        try:
            tool_id = payload.get("tool_id")
            logger.info(f"Tool registered: {tool_id}")

            # Could trigger dependency checks, validation, etc.

        except Exception as e:
            logger.error(f"Failed to handle tool registered: {e}")

    async def _handle_tool_executed(self, payload: Dict) -> None:
        """Handle tool executed event"""
        try:
            execution_id = payload.get("execution_id")
            tool_id = payload.get("tool_id")
            duration_ms = payload.get("duration_ms", 0)

            logger.info(f"Tool executed: {tool_id} ({execution_id})")

            # Record performance metrics
            if self.store and tool_id:
                metric_id = f"exec_time_{execution_id}"
                await self.store.record_performance_metric(
                    metric_id=metric_id,
                    resource_type="tool",
                    resource_id=tool_id,
                    metric_type="execution_time_ms",
                    metric_value=duration_ms
                )

        except Exception as e:
            logger.error(f"Failed to handle tool executed: {e}")

    async def _handle_skill_registered(self, payload: Dict) -> None:
        """Handle skill registered event"""
        try:
            skill_id = payload.get("skill_id")
            logger.info(f"Skill registered: {skill_id}")

            # Validate skill dependencies
            if self.skill_manager:
                await self.skill_manager.validate_dependencies(skill_id)

        except Exception as e:
            logger.error(f"Failed to handle skill registered: {e}")

    async def _handle_skill_executed(self, payload: Dict) -> None:
        """Handle skill executed event"""
        try:
            execution_id = payload.get("execution_id")
            skill_id = payload.get("skill_id")

            logger.info(f"Skill executed: {skill_id} ({execution_id})")

        except Exception as e:
            logger.error(f"Failed to handle skill executed: {e}")

    async def _handle_skill_feedback(self, payload: Dict) -> None:
        """Handle skill feedback event"""
        try:
            execution_id = payload.get("execution_id")
            feedback = payload.get("feedback", {})

            logger.info(f"Skill feedback received: {execution_id}")

            # Store feedback in database
            if self.store:
                await self.store.add_skill_feedback(execution_id, feedback)

        except Exception as e:
            logger.error(f"Failed to handle skill feedback: {e}")

    async def _handle_runtime_error(self, payload: Dict) -> None:
        """Handle runtime error event"""
        try:
            error_type = payload.get("error_type")
            resource_id = payload.get("resource_id")

            logger.warning(f"Runtime error: {error_type} for {resource_id}")

            # Could trigger automatic recovery, notifications, etc.

        except Exception as e:
            logger.error(f"Failed to handle runtime error: {e}")

    # Tool Manager Interface (from tools/)
    async def register_tool(self, tool_id: str, tool_name: str,
                          tool_type: str, tool_spec: Dict,
                          version: str = "1.0", metadata: Optional[Dict] = None) -> None:
        """Register a new tool"""
        return await self.tool_manager.register_tool(
            tool_id, tool_name, tool_type, tool_spec, version, metadata
        )

    async def get_tool(self, tool_id: str) -> Optional[Dict]:
        """Get tool definition by ID"""
        return await self.tool_manager.get_tool(tool_id)

    async def list_tools(self, tool_type: Optional[str] = None) -> List[Dict]:
        """List available tools"""
        return await self.tool_manager.list_tools(tool_type)

    async def update_tool(self, tool_id: str, updates: Dict) -> None:
        """Update tool definition"""
        return await self.tool_manager.update_tool(tool_id, updates)

    async def enable_tool(self, tool_id: str) -> None:
        """Enable a tool"""
        return await self.tool_manager.enable_tool(tool_id)

    async def disable_tool(self, tool_id: str) -> None:
        """Disable a tool"""
        return await self.tool_manager.disable_tool(tool_id)

    # Tool Runtime Interface (from tool-runtime/)
    async def execute_tool(self, tool_id: str, input_data: Dict,
                         execution_context: Optional[Dict] = None) -> Dict:
        """Execute a tool"""
        return await self.tool_runtime.execute_tool(tool_id, input_data, execution_context)

    async def get_tool_execution(self, execution_id: str) -> Optional[Dict]:
        """Get tool execution details"""
        return await self.tool_runtime.get_execution(execution_id)

    async def abort_tool_execution(self, execution_id: str) -> bool:
        """Abort a running tool execution"""
        return await self.tool_runtime.abort_execution(execution_id)

    async def get_tool_performance(self, tool_id: str) -> Dict:
        """Get tool performance metrics"""
        return await self.tool_runtime.get_performance_metrics(tool_id)

    # Skill Manager Interface (from skill-runtime/)
    async def register_skill(self, skill_id: str, skill_name: str,
                           skill_type: str, skill_manifest: Dict,
                           dependencies: List[str], version: str = "1.0",
                           metadata: Optional[Dict] = None) -> None:
        """Register a new skill"""
        return await self.skill_manager.register_skill(
            skill_id, skill_name, skill_type, skill_manifest, dependencies, version, metadata
        )

    async def get_skill(self, skill_id: str) -> Optional[Dict]:
        """Get skill definition by ID"""
        return await self.skill_manager.get_skill(skill_id)

    async def list_skills(self, enabled_only: bool = True) -> List[Dict]:
        """List available skills"""
        return await self.skill_manager.list_skills(enabled_only)

    async def execute_skill(self, skill_id: str, input_data: Dict,
                          context_pack: Optional[Dict] = None) -> Dict:
        """Execute a skill"""
        return await self.skill_manager.execute_skill(skill_id, input_data, context_pack)

    async def get_skill_execution(self, execution_id: str) -> Optional[Dict]:
        """Get skill execution details"""
        return await self.skill_manager.get_execution(execution_id)

    async def submit_skill_feedback(self, execution_id: str, feedback: Dict) -> None:
        """Submit feedback for a skill execution"""
        return await self.skill_manager.submit_feedback(execution_id, feedback)

    async def get_skill_performance(self, skill_id: str) -> Dict:
        """Get skill performance metrics"""
        return await self.skill_manager.get_performance_metrics(skill_id)

    # Tool Discovery and Dependencies
    async def discover_tools_for_skill(self, skill_id: str) -> List[Dict]:
        """Discover available tools for a skill"""
        skill = await self.get_skill(skill_id)
        if not skill:
            return []

        dependencies = skill.get("dependencies", [])
        available_tools = []

        for tool_id in dependencies:
            tool = await self.get_tool(tool_id)
            if tool and tool.get("enabled", False):
                available_tools.append(tool)

        return available_tools

    async def validate_skill_dependencies(self, skill_id: str) -> Dict:
        """Validate that all skill dependencies are available"""
        skill = await self.get_skill(skill_id)
        if not skill:
            return {"valid": False, "error": "Skill not found"}

        dependencies = skill.get("dependencies", [])
        validation_result = {
            "valid": True,
            "missing_tools": [],
            "disabled_tools": [],
            "total_dependencies": len(dependencies)
        }

        for tool_id in dependencies:
            tool = await self.get_tool(tool_id)
            if not tool:
                validation_result["missing_tools"].append(tool_id)
                validation_result["valid"] = False
            elif not tool.get("enabled", False):
                validation_result["disabled_tools"].append(tool_id)
                validation_result["valid"] = False

        return validation_result

    # Runtime Configuration
    async def get_runtime_configuration(self) -> Dict:
        """Get tool platform runtime configuration"""
        if not self.store:
            return {}

        config = await self.store.get_runtime_config("platform_config")
        if config:
            return config["config_data"]

        # Return default configuration
        return {
            "max_concurrent_tool_executions": 10,
            "max_concurrent_skill_executions": 5,
            "tool_execution_timeout_ms": 30000,
            "skill_execution_timeout_ms": 300000,
            "enable_performance_monitoring": True,
            "enable_execution_caching": True
        }

    async def update_runtime_configuration(self, config_updates: Dict) -> None:
        """Update runtime configuration"""
        if not self.store:
            return

        current_config = await self.get_runtime_configuration()
        updated_config = {**current_config, **config_updates}

        await self.store.create_runtime_config(
            config_id="platform_config",
            config_type="platform",
            config_data=updated_config
        )

        logger.info("Runtime configuration updated")

    # Analytics and Reporting
    async def get_platform_statistics(self) -> Dict:
        """Get platform usage statistics"""
        try:
            tools = await self.list_tools()
            skills = await self.list_skills()

            stats = {
                "total_tools": len(tools),
                "enabled_tools": len([t for t in tools if t.get("enabled", False)]),
                "total_skills": len(skills),
                "enabled_skills": len([s for s in skills if s.get("enabled", False)]),
                "tool_types": {},
                "skill_types": {}
            }

            # Count by type
            for tool in tools:
                tool_type = tool.get("tool_type", "unknown")
                stats["tool_types"][tool_type] = stats["tool_types"].get(tool_type, 0) + 1

            for skill in skills:
                skill_type = skill.get("skill_type", "unknown")
                stats["skill_types"][skill_type] = stats["skill_types"].get(skill_type, 0) + 1

            return stats

        except Exception as e:
            logger.error(f"Failed to get platform statistics: {e}")
            return {}

    async def generate_performance_report(self, timeframe_hours: int = 24) -> Dict:
        """Generate performance report"""
        try:
            # This would aggregate performance metrics from the database
            return {
                "timeframe_hours": timeframe_hours,
                "tool_executions": 0,
                "skill_executions": 0,
                "avg_tool_execution_time_ms": 0,
                "avg_skill_execution_time_ms": 0,
                "error_rate": 0.0,
                "top_performing_tools": [],
                "top_performing_skills": []
            }

        except Exception as e:
            logger.error(f"Failed to generate performance report: {e}")
            return {}

    # Platform Status
    async def get_platform_status(self) -> Dict:
        """Get tool platform status"""
        try:
            base_status = {
                "status": "healthy",
                "modules": {},
                "statistics": {},
                "configuration": {}
            }

            # Module status
            if self.tool_manager:
                base_status["modules"]["tool_manager"] = self.tool_manager.is_healthy()
            if self.tool_runtime:
                base_status["modules"]["tool_runtime"] = self.tool_runtime.is_healthy()
            if self.skill_manager:
                base_status["modules"]["skill_manager"] = self.skill_manager.is_healthy()

            # Statistics
            base_status["statistics"] = await self.get_platform_statistics()

            # Configuration
            base_status["configuration"] = await self.get_runtime_configuration()

            return base_status

        except Exception as e:
            logger.error(f"Failed to get platform status: {e}")
            return {"status": "unhealthy", "error": str(e)}

    async def shutdown(self) -> None:
        """Shutdown the tool platform service"""
        try:
            # Stop event consumer
            if self.event_bus:
                self.event_bus.stop_consumer()

            # Shutdown modules
            if self.tool_manager:
                await self.tool_manager.shutdown()
            if self.tool_runtime:
                await self.tool_runtime.shutdown()
            if self.skill_manager:
                await self.skill_manager.shutdown()

            # Shutdown infrastructure
            if self.event_bus:
                await self.event_bus.shutdown()

            # Close store
            if self.store:
                await self.store.close()

            logger.info("Tool platform service shutdown complete")

        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            raise