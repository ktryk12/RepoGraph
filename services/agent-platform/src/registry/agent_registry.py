"""
Agent Registry Module

Consolidated from services/agent-registry/
Provides agent discovery, registration, and health monitoring functionality
with PostgreSQL persistence.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class AgentRegistry:
    """
    Agent registry and discovery service

    Consolidated functionality from agent-registry service:
    - Agent instance registration
    - Agent discovery with capability filtering
    - Health monitoring and status updates
    - Endpoint management
    """

    def __init__(self, store):
        self.store = store
        self.health_check_interval = 30  # seconds
        self.health_check_task: Optional[asyncio.Task] = None
        self._running = False

    async def initialize(self) -> None:
        """Initialize the registry module"""
        try:
            self._running = True

            # Start periodic health checks
            self.health_check_task = asyncio.create_task(self._periodic_health_check())

            logger.info("Agent registry module initialized")

        except Exception as e:
            logger.error(f"Failed to initialize agent registry: {e}")
            raise

    # Agent Registration (from agent-registry/src/registry.py)
    async def register_agent_instance(self, registry_id: str, agent_id: str,
                                    endpoint_url: str, capabilities: Dict) -> None:
        """
        Register an agent instance in the registry

        Args:
            registry_id: Unique ID for this registration
            agent_id: Reference to agent definition
            endpoint_url: HTTP endpoint for agent communication
            capabilities: Agent capabilities and metadata
        """
        try:
            await self.store.register_agent(registry_id, agent_id, endpoint_url, capabilities)

            # Set initial health status
            await self.store.update_agent_health(registry_id, "healthy")

            logger.info(f"Registered agent instance {registry_id} for agent {agent_id}")

        except Exception as e:
            logger.error(f"Failed to register agent instance {registry_id}: {e}")
            raise

    async def unregister_agent_instance(self, registry_id: str) -> None:
        """Unregister an agent instance"""
        try:
            # Mark as unhealthy before potential removal
            await self.store.update_agent_health(registry_id, "unregistered")
            logger.info(f"Unregistered agent instance {registry_id}")

        except Exception as e:
            logger.error(f"Failed to unregister agent instance {registry_id}: {e}")
            raise

    # Agent Discovery (from agent-registry/src/consumer.py)
    async def discover_agents(self, capabilities_filter: Optional[Dict] = None) -> List[Dict]:
        """
        Discover available agent instances

        Args:
            capabilities_filter: Filter agents by required capabilities

        Returns:
            List of available agent instances with metadata
        """
        try:
            agents = await self.store.discover_agents(capabilities_filter)

            # Filter by health status - only return healthy agents
            healthy_agents = [
                agent for agent in agents
                if agent.get("health_status") == "healthy"
            ]

            logger.debug(f"Discovered {len(healthy_agents)} healthy agents")
            return healthy_agents

        except Exception as e:
            logger.error(f"Failed to discover agents: {e}")
            raise

    async def get_agent_instances(self, agent_id: str) -> List[Dict]:
        """Get all instances for a specific agent ID"""
        try:
            all_agents = await self.store.discover_agents()
            agent_instances = [
                agent for agent in all_agents
                if agent.get("agent_id") == agent_id
            ]

            return agent_instances

        except Exception as e:
            logger.error(f"Failed to get instances for agent {agent_id}: {e}")
            raise

    async def get_agent_endpoint(self, agent_id: str) -> Optional[str]:
        """
        Get a healthy endpoint for agent execution

        Returns the endpoint URL of a healthy instance, or None if none available
        """
        try:
            instances = await self.get_agent_instances(agent_id)
            healthy_instances = [
                instance for instance in instances
                if instance.get("health_status") == "healthy"
            ]

            if healthy_instances:
                # Return first healthy instance (could implement load balancing here)
                return healthy_instances[0].get("endpoint_url")

            logger.warning(f"No healthy instances found for agent {agent_id}")
            return None

        except Exception as e:
            logger.error(f"Failed to get endpoint for agent {agent_id}: {e}")
            return None

    # Health Management (from agent-registry/src/deployer.py)
    async def update_agent_health(self, registry_id: str, health_status: str) -> None:
        """Update agent health status"""
        try:
            await self.store.update_agent_health(registry_id, health_status)
            logger.debug(f"Updated health for {registry_id}: {health_status}")

        except Exception as e:
            logger.error(f"Failed to update health for {registry_id}: {e}")
            raise

    async def check_agent_health(self, registry_id: str, endpoint_url: str) -> str:
        """
        Check health of a specific agent instance

        Returns: 'healthy', 'unhealthy', or 'unreachable'
        """
        try:
            # Implement health check logic
            # This would typically involve HTTP health check to the endpoint
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=5)  # 5 second timeout
            async with aiohttp.ClientSession(timeout=timeout) as session:
                health_endpoint = f"{endpoint_url}/health"

                async with session.get(health_endpoint) as response:
                    if response.status == 200:
                        return "healthy"
                    else:
                        return "unhealthy"

        except Exception as e:
            logger.warning(f"Health check failed for {registry_id}: {e}")
            return "unreachable"

    async def _periodic_health_check(self) -> None:
        """Periodic health check task"""
        while self._running:
            try:
                # Get all registered agents
                all_agents = await self.store.discover_agents()

                # Check health for each agent
                health_check_tasks = []
                for agent in all_agents:
                    registry_id = agent.get("registry_id")
                    endpoint_url = agent.get("endpoint_url")

                    if registry_id and endpoint_url:
                        task = self._check_and_update_health(registry_id, endpoint_url)
                        health_check_tasks.append(task)

                # Wait for all health checks to complete
                if health_check_tasks:
                    await asyncio.gather(*health_check_tasks, return_exceptions=True)

                # Wait before next health check cycle
                await asyncio.sleep(self.health_check_interval)

            except Exception as e:
                logger.error(f"Error in periodic health check: {e}")
                await asyncio.sleep(self.health_check_interval)

    async def _check_and_update_health(self, registry_id: str, endpoint_url: str) -> None:
        """Check and update health for a single agent"""
        try:
            health_status = await self.check_agent_health(registry_id, endpoint_url)
            await self.update_agent_health(registry_id, health_status)

        except Exception as e:
            logger.warning(f"Failed to check/update health for {registry_id}: {e}")

    # Registry Statistics and Management
    async def get_registry_statistics(self) -> Dict:
        """Get registry statistics"""
        try:
            all_agents = await self.store.discover_agents()

            stats = {
                "total_registered": len(all_agents),
                "healthy": len([a for a in all_agents if a.get("health_status") == "healthy"]),
                "unhealthy": len([a for a in all_agents if a.get("health_status") == "unhealthy"]),
                "unreachable": len([a for a in all_agents if a.get("health_status") == "unreachable"]),
                "by_type": {}
            }

            # Group by agent type
            for agent in all_agents:
                agent_type = agent.get("agent_type", "unknown")
                if agent_type not in stats["by_type"]:
                    stats["by_type"][agent_type] = 0
                stats["by_type"][agent_type] += 1

            return stats

        except Exception as e:
            logger.error(f"Failed to get registry statistics: {e}")
            return {}

    def is_healthy(self) -> bool:
        """Check if registry module is healthy"""
        return self._running and self.store is not None

    async def shutdown(self) -> None:
        """Shutdown the registry module"""
        try:
            self._running = False

            if self.health_check_task:
                self.health_check_task.cancel()
                try:
                    await self.health_check_task
                except asyncio.CancelledError:
                    pass

            logger.info("Agent registry module shutdown complete")

        except Exception as e:
            logger.error(f"Error during registry shutdown: {e}")
            raise