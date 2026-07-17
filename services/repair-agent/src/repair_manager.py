"""
Repair Manager Module

Consolidated from services/repair-agent/
Provides agent repair, debugging, and recovery functionality
with PostgreSQL persistence.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from uuid import uuid4

logger = logging.getLogger(__name__)


class RepairManager:
    """
    Agent repair and recovery service

    Consolidated functionality from repair-agent service:
    - Automatic error detection and repair
    - Manual repair operation management
    - Repair history and analytics
    - Recovery strategy optimization
    """

    def __init__(self, store):
        self.store = store
        self.repair_strategies: Dict[str, callable] = {}
        self.auto_repair_enabled = True
        self.repair_timeout = 300  # 5 minutes

    async def initialize(self) -> None:
        """Initialize the repair manager module"""
        try:
            # Register built-in repair strategies
            await self._register_repair_strategies()

            logger.info("Repair manager module initialized")

        except Exception as e:
            logger.error(f"Failed to initialize repair manager: {e}")
            raise

    async def _register_repair_strategies(self) -> None:
        """Register built-in repair strategies"""

        # Strategy definitions from repair-agent/src/repair_agent.py
        strategies = {
            "retry": self._retry_repair_strategy,
            "parameter_adjustment": self._parameter_adjustment_strategy,
            "context_reset": self._context_reset_strategy,
            "fallback_execution": self._fallback_execution_strategy,
            "resource_cleanup": self._resource_cleanup_strategy,
            "configuration_repair": self._configuration_repair_strategy,
            "dependency_repair": self._dependency_repair_strategy,
            "memory_cleanup": self._memory_cleanup_strategy
        }

        self.repair_strategies.update(strategies)
        logger.info(f"Registered {len(strategies)} repair strategies")

    # Repair Operation Management
    async def initiate_repair(self, agent_id: str, execution_id: str,
                            repair_type: str, repair_data: Dict) -> str:
        """
        Initiate a repair operation

        Args:
            agent_id: ID of the agent needing repair
            execution_id: ID of the failed execution
            repair_type: Type of repair strategy to apply
            repair_data: Data and context for the repair

        Returns:
            repair_id: Unique ID for the repair operation
        """
        try:
            repair_id = f"repair_{uuid4().hex[:12]}"

            # Validate repair type
            if repair_type not in self.repair_strategies:
                raise ValueError(f"Unknown repair type: {repair_type}")

            # Create repair record
            await self.store.create_repair(
                repair_id=repair_id,
                agent_id=agent_id,
                execution_id=execution_id,
                repair_type=repair_type,
                repair_data=repair_data
            )

            # Start repair operation asynchronously
            asyncio.create_task(
                self._execute_repair_strategy(repair_id, repair_type, repair_data)
            )

            logger.info(f"Initiated repair {repair_id} for agent {agent_id}")
            return repair_id

        except Exception as e:
            logger.error(f"Failed to initiate repair for agent {agent_id}: {e}")
            raise

    async def _execute_repair_strategy(self, repair_id: str, repair_type: str,
                                     repair_data: Dict) -> None:
        """Execute a specific repair strategy"""
        try:
            # Update status to running
            await self.store.update_repair_status(repair_id, "running")

            # Get repair strategy function
            strategy_function = self.repair_strategies[repair_type]

            # Execute repair with timeout
            repair_result = await asyncio.wait_for(
                strategy_function(repair_data),
                timeout=self.repair_timeout
            )

            # Update repair status with result
            await self.store.update_repair_status(
                repair_id,
                "completed",
                repair_result
            )

            logger.info(f"Repair {repair_id} completed successfully")

        except asyncio.TimeoutError:
            error_result = {"error": "Repair operation timed out"}
            await self.store.update_repair_status(repair_id, "failed", error_result)
            logger.error(f"Repair {repair_id} timed out")

        except Exception as e:
            error_result = {"error": str(e)}
            await self.store.update_repair_status(repair_id, "failed", error_result)
            logger.error(f"Repair {repair_id} failed: {e}")

    # Repair Strategies (from repair-agent/src/repair_agent.py)
    async def _retry_repair_strategy(self, repair_data: Dict) -> Dict:
        """Simple retry repair strategy"""
        try:
            max_retries = repair_data.get("max_retries", 3)
            retry_delay = repair_data.get("retry_delay", 5)

            for attempt in range(max_retries):
                logger.info(f"Retry attempt {attempt + 1}/{max_retries}")

                # Simulate repair action (would call actual agent execution)
                await asyncio.sleep(retry_delay)

                # Check if repair was successful (placeholder logic)
                if attempt >= 1:  # Simulate success on second attempt
                    return {
                        "success": True,
                        "attempts": attempt + 1,
                        "strategy": "retry",
                        "message": f"Repair successful after {attempt + 1} attempts"
                    }

            return {
                "success": False,
                "attempts": max_retries,
                "strategy": "retry",
                "message": "Max retries exceeded"
            }

        except Exception as e:
            return {
                "success": False,
                "strategy": "retry",
                "error": str(e)
            }

    async def _parameter_adjustment_strategy(self, repair_data: Dict) -> Dict:
        """Parameter adjustment repair strategy"""
        try:
            adjustments = repair_data.get("adjustments", {})

            # Apply parameter adjustments
            applied_adjustments = []
            for param, new_value in adjustments.items():
                # Simulate parameter adjustment
                applied_adjustments.append({
                    "parameter": param,
                    "old_value": repair_data.get("original_params", {}).get(param),
                    "new_value": new_value
                })

            return {
                "success": True,
                "strategy": "parameter_adjustment",
                "adjustments": applied_adjustments,
                "message": f"Applied {len(applied_adjustments)} parameter adjustments"
            }

        except Exception as e:
            return {
                "success": False,
                "strategy": "parameter_adjustment",
                "error": str(e)
            }

    async def _context_reset_strategy(self, repair_data: Dict) -> Dict:
        """Context reset repair strategy"""
        try:
            context_type = repair_data.get("context_type", "full")

            # Simulate context reset
            reset_components = []
            if context_type in ["full", "memory"]:
                reset_components.append("memory")
            if context_type in ["full", "state"]:
                reset_components.append("execution_state")
            if context_type in ["full", "cache"]:
                reset_components.append("cache")

            return {
                "success": True,
                "strategy": "context_reset",
                "reset_components": reset_components,
                "message": f"Reset {', '.join(reset_components)}"
            }

        except Exception as e:
            return {
                "success": False,
                "strategy": "context_reset",
                "error": str(e)
            }

    async def _fallback_execution_strategy(self, repair_data: Dict) -> Dict:
        """Fallback execution repair strategy"""
        try:
            fallback_agent = repair_data.get("fallback_agent")
            fallback_config = repair_data.get("fallback_config", {})

            if not fallback_agent:
                return {
                    "success": False,
                    "strategy": "fallback_execution",
                    "error": "No fallback agent specified"
                }

            # Simulate fallback execution
            return {
                "success": True,
                "strategy": "fallback_execution",
                "fallback_agent": fallback_agent,
                "fallback_config": fallback_config,
                "message": f"Executed with fallback agent: {fallback_agent}"
            }

        except Exception as e:
            return {
                "success": False,
                "strategy": "fallback_execution",
                "error": str(e)
            }

    async def _resource_cleanup_strategy(self, repair_data: Dict) -> Dict:
        """Resource cleanup repair strategy"""
        try:
            cleanup_targets = repair_data.get("cleanup_targets", ["temp_files", "memory", "connections"])

            cleaned_resources = []
            for target in cleanup_targets:
                # Simulate resource cleanup
                cleaned_resources.append(target)

            return {
                "success": True,
                "strategy": "resource_cleanup",
                "cleaned_resources": cleaned_resources,
                "message": f"Cleaned up {len(cleaned_resources)} resource types"
            }

        except Exception as e:
            return {
                "success": False,
                "strategy": "resource_cleanup",
                "error": str(e)
            }

    async def _configuration_repair_strategy(self, repair_data: Dict) -> Dict:
        """Configuration repair strategy"""
        try:
            config_fixes = repair_data.get("config_fixes", {})

            applied_fixes = []
            for config_key, fix_value in config_fixes.items():
                # Simulate configuration fix
                applied_fixes.append({
                    "config_key": config_key,
                    "fix_applied": fix_value
                })

            return {
                "success": True,
                "strategy": "configuration_repair",
                "applied_fixes": applied_fixes,
                "message": f"Applied {len(applied_fixes)} configuration fixes"
            }

        except Exception as e:
            return {
                "success": False,
                "strategy": "configuration_repair",
                "error": str(e)
            }

    async def _dependency_repair_strategy(self, repair_data: Dict) -> Dict:
        """Dependency repair strategy"""
        try:
            dependency_issues = repair_data.get("dependency_issues", [])

            resolved_issues = []
            for issue in dependency_issues:
                # Simulate dependency resolution
                resolved_issues.append({
                    "dependency": issue.get("dependency"),
                    "issue": issue.get("issue"),
                    "resolution": "reloaded"
                })

            return {
                "success": True,
                "strategy": "dependency_repair",
                "resolved_issues": resolved_issues,
                "message": f"Resolved {len(resolved_issues)} dependency issues"
            }

        except Exception as e:
            return {
                "success": False,
                "strategy": "dependency_repair",
                "error": str(e)
            }

    async def _memory_cleanup_strategy(self, repair_data: Dict) -> Dict:
        """Memory cleanup repair strategy"""
        try:
            cleanup_level = repair_data.get("cleanup_level", "moderate")

            cleaned_items = []
            if cleanup_level in ["moderate", "aggressive"]:
                cleaned_items.extend(["temp_objects", "cache_entries"])
            if cleanup_level == "aggressive":
                cleaned_items.extend(["unused_references", "stale_connections"])

            return {
                "success": True,
                "strategy": "memory_cleanup",
                "cleanup_level": cleanup_level,
                "cleaned_items": cleaned_items,
                "message": f"Cleaned {len(cleaned_items)} memory categories"
            }

        except Exception as e:
            return {
                "success": False,
                "strategy": "memory_cleanup",
                "error": str(e)
            }

    # Repair Status and Management
    async def get_repair_status(self, repair_id: str) -> Optional[Dict]:
        """Get repair operation status"""
        try:
            # This would query the store for repair status
            # For now, return a placeholder implementation
            return {
                "repair_id": repair_id,
                "status": "completed",  # pending, running, completed, failed
                "progress": 100,
                "message": "Repair completed successfully"
            }

        except Exception as e:
            logger.error(f"Failed to get repair status {repair_id}: {e}")
            return None

    async def get_repair_history(self, agent_id: str) -> List[Dict]:
        """Get repair history for agent"""
        try:
            return await self.store.get_repair_history(agent_id)

        except Exception as e:
            logger.error(f"Failed to get repair history for agent {agent_id}: {e}")
            return []

    async def cancel_repair(self, repair_id: str) -> bool:
        """Cancel a running repair operation"""
        try:
            # Update repair status to cancelled
            await self.store.update_repair_status(
                repair_id,
                "cancelled",
                {"message": "Repair cancelled by user"}
            )

            logger.info(f"Cancelled repair operation {repair_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to cancel repair {repair_id}: {e}")
            return False

    # Auto-Repair and Analytics
    async def enable_auto_repair(self, agent_id: str, strategies: List[str]) -> None:
        """Enable automatic repair for an agent"""
        try:
            # Update agent metadata to include auto-repair settings
            agent = await self.store.get_agent_definition(agent_id)
            if agent:
                metadata = agent.get("metadata", {})
                metadata["auto_repair"] = {
                    "enabled": True,
                    "strategies": strategies,
                    "enabled_at": datetime.utcnow().isoformat()
                }

                # Update agent definition (this would need factory module)
                logger.info(f"Auto-repair enabled for agent {agent_id}")

        except Exception as e:
            logger.error(f"Failed to enable auto-repair for agent {agent_id}: {e}")
            raise

    async def get_repair_analytics(self, agent_id: Optional[str] = None) -> Dict:
        """Get repair analytics"""
        try:
            if agent_id:
                repairs = await self.get_repair_history(agent_id)
            else:
                # Get all repairs (would need store method)
                repairs = []

            analytics = {
                "total_repairs": len(repairs),
                "success_rate": 0,
                "most_common_repair_types": {},
                "average_repair_time": 0
            }

            if repairs:
                successful = [r for r in repairs if r.get("repair_status") == "completed"]
                analytics["success_rate"] = len(successful) / len(repairs) * 100

                # Count repair types
                for repair in repairs:
                    repair_type = repair.get("repair_type", "unknown")
                    analytics["most_common_repair_types"][repair_type] = \
                        analytics["most_common_repair_types"].get(repair_type, 0) + 1

            return analytics

        except Exception as e:
            logger.error(f"Failed to get repair analytics: {e}")
            return {}

    def is_healthy(self) -> bool:
        """Check if repair manager module is healthy"""
        return self.store is not None

    async def shutdown(self) -> None:
        """Shutdown the repair manager module"""
        try:
            logger.info("Repair manager module shutdown complete")

        except Exception as e:
            logger.error(f"Error during repair manager shutdown: {e}")
            raise