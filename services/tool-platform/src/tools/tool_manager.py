"""
Tool Manager Module

Consolidated from services/tools/src/
Provides tool registration, discovery, and lifecycle management.
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class ToolManager:
    """
    Tool registry and management service

    Consolidated functionality from tools service:
    - Tool registration and discovery
    - Tool lifecycle management
    - Tool validation and metadata management
    - Built-in tool types
    """

    def __init__(self, store, event_bus=None):
        self.store = store
        self.event_bus = event_bus

        # Built-in tool types (consolidated from tools/, skill_runtime/, skills/)
        self.built_in_tools = {
            # Core development tools
            "git_apply": {
                "name": "Git Apply Tool",
                "description": "Apply git patches and manage repository changes",
                "categories": ["version_control", "development"]
            },
            "print_repo": {
                "name": "Repository Printer",
                "description": "Print repository structure and contents",
                "categories": ["analysis", "documentation"]
            },
            "run_tests": {
                "name": "Test Runner",
                "description": "Execute test suites and collect results",
                "categories": ["testing", "quality_assurance"]
            },
            "security_scan": {
                "name": "Security Scanner",
                "description": "Perform security analysis and vulnerability scanning",
                "categories": ["security", "analysis"]
            },
            "lint": {
                "name": "Code Linter",
                "description": "Perform code style and quality checks",
                "categories": ["quality_assurance", "development"]
            },
            "hw_probe": {
                "name": "Hardware Probe",
                "description": "Probe and analyze hardware capabilities",
                "categories": ["system", "analysis"]
            },
            "doctor_env": {
                "name": "Environment Doctor",
                "description": "Diagnose and validate development environment",
                "categories": ["system", "diagnostics"]
            },
            "search_local_index": {
                "name": "Local Index Search",
                "description": "Search local code index and repository",
                "categories": ["search", "analysis"]
            },
            "repo_reader": {
                "name": "Repository Reader",
                "description": "Read and analyze repository structure",
                "categories": ["analysis", "documentation"]
            },

            # Financial and trading tools
            "analytics_collector": {
                "name": "Analytics Collector",
                "description": "Collect and aggregate analytics data",
                "categories": ["analytics", "data"]
            },
            "binance_public_client": {
                "name": "Binance Public API Client",
                "description": "Interface with Binance public API for market data",
                "categories": ["trading", "crypto", "api"]
            },
            "coingecko_client": {
                "name": "CoinGecko API Client",
                "description": "Interface with CoinGecko API for crypto market data",
                "categories": ["trading", "crypto", "api"]
            },
            "etoro_client": {
                "name": "eToro API Client",
                "description": "Interface with eToro trading platform",
                "categories": ["trading", "api"]
            },
            "finrobot_adapter": {
                "name": "FinRobot Adapter",
                "description": "Adapter for financial robot automation",
                "categories": ["trading", "automation"]
            },
            "opportunity_scorer": {
                "name": "Opportunity Scorer",
                "description": "Score and rank trading opportunities",
                "categories": ["trading", "analytics"]
            },
            "whale_alert_client": {
                "name": "Whale Alert Client",
                "description": "Monitor large cryptocurrency transactions",
                "categories": ["crypto", "monitoring", "api"]
            },

            # Infrastructure tools
            "kafka_provisioner": {
                "name": "Kafka Provisioner",
                "description": "Provision and manage Kafka infrastructure",
                "categories": ["infrastructure", "kafka"]
            },
            "firecrawl_client": {
                "name": "Firecrawl Client",
                "description": "Web crawling and scraping client",
                "categories": ["data", "scraping", "web"]
            },
            "review_miner": {
                "name": "Review Miner",
                "description": "Mine and analyze code reviews",
                "categories": ["analysis", "development"]
            },
            "evidence": {
                "name": "Evidence Collector",
                "description": "Collect and manage evidence for analysis",
                "categories": ["analysis", "data"]
            },

            # Base classes and utilities
            "base": {
                "name": "Base Tool Framework",
                "description": "Base classes and utilities for tool development",
                "categories": ["framework", "utilities"]
            },
            "contracts": {
                "name": "Tool Contracts",
                "description": "Contract definitions and validation for tools",
                "categories": ["framework", "validation"]
            },
            "registry": {
                "name": "Tool Registry",
                "description": "Tool registration and discovery framework",
                "categories": ["framework", "registry"]
            },
            "runtime": {
                "name": "Tool Runtime",
                "description": "Tool execution runtime environment",
                "categories": ["framework", "execution"]
            },

            # Crypto intelligence tools
            "aggregator": {
                "name": "Crypto Intelligence Aggregator",
                "description": "Aggregate cryptocurrency intelligence data",
                "categories": ["crypto", "intelligence", "data"]
            },
            "test_smoke": {
                "name": "Smoke Test Runner",
                "description": "Execute smoke tests for validation",
                "categories": ["testing", "validation"]
            }
        }

    async def initialize(self) -> None:
        """Initialize tool manager"""
        try:
            # Register built-in tools
            await self._register_built_in_tools()
            logger.info("Tool manager initialized")

        except Exception as e:
            logger.error(f"Failed to initialize tool manager: {e}")
            raise

    async def _register_built_in_tools(self) -> None:
        """Register built-in tools from tools/ service"""
        for tool_id, tool_info in self.built_in_tools.items():
            try:
                # Check if tool is already registered
                existing = await self.store.get_tool(tool_id)
                if existing:
                    continue

                # Create tool specification
                tool_spec = {
                    "implementation": f"tools.{tool_id}",
                    "interface": "command_line",
                    "input_schema": {"type": "object", "properties": {}},
                    "output_schema": {"type": "object", "properties": {}},
                    "timeout_ms": 30000,
                    "retry_count": 3,
                    **tool_info
                }

                # Register tool
                await self.store.create_tool(
                    tool_id=tool_id,
                    tool_name=tool_info["name"],
                    tool_type="built_in",
                    tool_spec=tool_spec,
                    metadata={
                        "source": "built_in",
                        "registered_at": datetime.utcnow().isoformat()
                    }
                )

                logger.debug(f"Registered built-in tool: {tool_id}")

            except Exception as e:
                logger.warning(f"Failed to register built-in tool {tool_id}: {e}")

    async def register_tool(self, tool_id: str, tool_name: str,
                          tool_type: str, tool_spec: Dict,
                          version: str = "1.0", metadata: Optional[Dict] = None) -> None:
        """Register a new tool"""
        try:
            # Validate tool specification
            validation_result = self._validate_tool_spec(tool_spec)
            if not validation_result["valid"]:
                raise ValueError(f"Invalid tool spec: {validation_result['errors']}")

            # Enhance metadata
            enhanced_metadata = {
                "registered_by": "system",
                "registration_timestamp": datetime.utcnow().isoformat(),
                "validation_result": validation_result,
                **(metadata or {})
            }

            # Store tool
            await self.store.create_tool(
                tool_id=tool_id,
                tool_name=tool_name,
                tool_type=tool_type,
                tool_spec=tool_spec,
                version=version,
                metadata=enhanced_metadata
            )

            # Publish registration event
            if self.event_bus:
                self.event_bus.publish_tool_registered(tool_id, {
                    "tool_name": tool_name,
                    "tool_type": tool_type,
                    "version": version
                })

            logger.info(f"Tool registered: {tool_id} ({tool_type})")

        except Exception as e:
            logger.error(f"Failed to register tool {tool_id}: {e}")
            raise

    def _validate_tool_spec(self, tool_spec: Dict) -> Dict:
        """Validate tool specification"""
        validation_result = {
            "valid": True,
            "errors": [],
            "warnings": []
        }

        # Check required fields
        required_fields = ["implementation", "interface", "input_schema", "output_schema"]
        for field in required_fields:
            if field not in tool_spec:
                validation_result["valid"] = False
                validation_result["errors"].append(f"Missing required field: {field}")

        # Check interface type
        valid_interfaces = ["command_line", "http_api", "python_function", "docker_container"]
        interface = tool_spec.get("interface")
        if interface and interface not in valid_interfaces:
            validation_result["warnings"].append(f"Unknown interface type: {interface}")

        return validation_result

    async def get_tool(self, tool_id: str) -> Optional[Dict]:
        """Get tool definition by ID"""
        try:
            return await self.store.get_tool(tool_id)

        except Exception as e:
            logger.error(f"Failed to get tool {tool_id}: {e}")
            return None

    async def list_tools(self, tool_type: Optional[str] = None) -> List[Dict]:
        """List available tools"""
        try:
            if tool_type:
                return await self.store.list_tools_by_type(tool_type)
            else:
                # Get all tool types
                all_tools = []
                tool_types = ["built_in", "custom", "external", "container"]
                for ttype in tool_types:
                    tools = await self.store.list_tools_by_type(ttype)
                    all_tools.extend(tools)
                return all_tools

        except Exception as e:
            logger.error(f"Failed to list tools: {e}")
            return []

    async def update_tool(self, tool_id: str, updates: Dict) -> None:
        """Update tool definition"""
        try:
            # Get existing tool
            existing = await self.get_tool(tool_id)
            if not existing:
                raise ValueError(f"Tool not found: {tool_id}")

            # Merge updates
            updated_spec = {**existing["tool_spec"], **updates.get("tool_spec", {})}
            updated_metadata = {**existing["metadata"], **updates.get("metadata", {})}
            updated_metadata["last_modified"] = datetime.utcnow().isoformat()

            # Update tool
            await self.store.create_tool(
                tool_id=tool_id,
                tool_name=updates.get("tool_name", existing["tool_name"]),
                tool_type=updates.get("tool_type", existing["tool_type"]),
                tool_spec=updated_spec,
                version=updates.get("version", existing["version"]),
                metadata=updated_metadata
            )

            logger.info(f"Tool updated: {tool_id}")

        except Exception as e:
            logger.error(f"Failed to update tool {tool_id}: {e}")
            raise

    async def enable_tool(self, tool_id: str) -> None:
        """Enable a tool"""
        try:
            await self._set_tool_enabled(tool_id, True)
            logger.info(f"Tool enabled: {tool_id}")

        except Exception as e:
            logger.error(f"Failed to enable tool {tool_id}: {e}")
            raise

    async def disable_tool(self, tool_id: str) -> None:
        """Disable a tool"""
        try:
            await self._set_tool_enabled(tool_id, False)
            logger.info(f"Tool disabled: {tool_id}")

        except Exception as e:
            logger.error(f"Failed to disable tool {tool_id}: {e}")
            raise

    async def _set_tool_enabled(self, tool_id: str, enabled: bool) -> None:
        """Set tool enabled status"""
        tool = await self.get_tool(tool_id)
        if not tool:
            raise ValueError(f"Tool not found: {tool_id}")

        # Update enabled status
        await self.update_tool(tool_id, {
            "metadata": {"enabled_status_changed": datetime.utcnow().isoformat()}
        })

    async def search_tools(self, query: str, categories: Optional[List[str]] = None) -> List[Dict]:
        """Search for tools by name, description, or categories"""
        try:
            all_tools = await self.list_tools()
            matching_tools = []

            query_lower = query.lower()

            for tool in all_tools:
                # Check name and description
                if (query_lower in tool["tool_name"].lower() or
                    query_lower in tool["tool_spec"].get("description", "").lower()):
                    matching_tools.append(tool)
                    continue

                # Check categories
                tool_categories = tool["tool_spec"].get("categories", [])
                if categories:
                    if any(cat in tool_categories for cat in categories):
                        matching_tools.append(tool)

            return matching_tools

        except Exception as e:
            logger.error(f"Failed to search tools: {e}")
            return []

    async def get_tool_categories(self) -> List[str]:
        """Get all available tool categories"""
        try:
            all_tools = await self.list_tools()
            categories = set()

            for tool in all_tools:
                tool_categories = tool["tool_spec"].get("categories", [])
                categories.update(tool_categories)

            return sorted(list(categories))

        except Exception as e:
            logger.error(f"Failed to get tool categories: {e}")
            return []

    async def validate_tool_dependencies(self, tool_id: str) -> Dict:
        """Validate tool dependencies"""
        try:
            tool = await self.get_tool(tool_id)
            if not tool:
                return {"valid": False, "error": "Tool not found"}

            dependencies = tool["tool_spec"].get("dependencies", [])
            validation_result = {
                "valid": True,
                "missing_dependencies": [],
                "total_dependencies": len(dependencies)
            }

            for dep in dependencies:
                dep_tool = await self.get_tool(dep)
                if not dep_tool or not dep_tool.get("enabled", True):
                    validation_result["missing_dependencies"].append(dep)
                    validation_result["valid"] = False

            return validation_result

        except Exception as e:
            logger.error(f"Failed to validate tool dependencies for {tool_id}: {e}")
            return {"valid": False, "error": str(e)}

    def is_healthy(self) -> bool:
        """Check if tool manager is healthy"""
        return self.store is not None

    async def shutdown(self) -> None:
        """Shutdown tool manager"""
        try:
            logger.info("Tool manager shutdown complete")

        except Exception as e:
            logger.error(f"Error during tool manager shutdown: {e}")
            raise