"""
Agent management endpoints for agent-platform.

Provides agent discovery, registration, and routing capabilities.
"""

from fastapi import APIRouter, HTTPException, Query
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
import uuid

# Handle missing shared libraries gracefully
try:
    from babyai_schemas import AgentTaskEvent, AgentCompletionEvent
except ImportError:
    # Mock event classes for testing
    class AgentTaskEvent:
        pass
    class AgentCompletionEvent:
        pass

try:
    from babyai_bus import create_event_bus_for_service
except ImportError:
    # Mock event bus for testing
    def create_event_bus_for_service(service_name):
        class MockEventBus:
            async def publish(self, event):
                pass
        return MockEventBus()

try:
    from ..agents.registry import AgentRegistry, AgentMetadata
except ImportError:
    # Fallback registry implementation for testing
    class AgentMetadata:
        def __init__(self, agent_id, role, **kwargs):
            self.agent_id = agent_id
            self.role = role
            self.status = "active"
            self.messages_processed = 0
            self.last_error = None

    class AgentRegistry:
        def __init__(self):
            self._agents = {}
            self._metadata = {}

        def register(self, agent):
            pass

        def get_by_role(self, role):
            return []

        def all(self):
            return []

        def get(self, agent_id):
            return None

router = APIRouter()

# Global registry instance (would be dependency injected in production)
registry = AgentRegistry()


class AgentRegistrationRequest(BaseModel):
    """Agent registration request."""
    agent_id: str
    service_name: str
    role: str
    capabilities: List[str]
    endpoint: str
    health_check: str = "/health"
    accepts_message_types: List[str] = []


class AgentDiscoveryRequest(BaseModel):
    """Agent discovery request parameters."""
    capability: Optional[str] = None
    service: Optional[str] = None
    role: Optional[str] = None
    available: Optional[bool] = True


class AgentEndpoint(BaseModel):
    """Agent endpoint information."""
    agent_id: str
    service_name: str
    role: str
    capabilities: List[str]
    endpoint: str
    status: str
    health_check: str


class AgentRequest(BaseModel):
    """Base agent request model."""
    task_type: str
    payload: Dict[str, Any]
    priority: str = "normal"
    context_id: str = None


class AgentResponse(BaseModel):
    """Base agent response model."""
    task_id: str
    status: str
    message: str


@router.post("/register")
async def register_agent(request: AgentRegistrationRequest):
    """Register a new agent in the system."""
    try:
        # Create a mock agent object for registration
        # In production, this would be a proper agent instance
        class MockAgent:
            def __init__(self, agent_id: str, role: str, endpoint: str):
                self.agent_id = agent_id
                self.role = role
                self.endpoint = endpoint
                self.capabilities = []

            def can_handle(self, message_type):
                return True  # Default implementation

        mock_agent = MockAgent(
            agent_id=request.agent_id,
            role=request.role,
            endpoint=request.endpoint
        )

        registry.register(mock_agent)

        # Publish registration event
        bus = create_event_bus_for_service("agent-platform")
        await bus.publish({
            "event_type": "agent_registered",
            "agent_id": request.agent_id,
            "service_name": request.service_name,
            "capabilities": request.capabilities,
            "endpoint": request.endpoint
        })

        return {"status": "registered", "agent_id": request.agent_id}

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Registration failed: {str(e)}")


@router.get("/discover", response_model=List[AgentEndpoint])
async def discover_agents(
    capability: Optional[str] = Query(None, description="Filter by capability"),
    service: Optional[str] = Query(None, description="Filter by service name"),
    role: Optional[str] = Query(None, description="Filter by role"),
    available: Optional[bool] = Query(True, description="Only available agents")
):
    """Discover agents based on criteria."""
    try:
        agents = []

        if role:
            # Get agents by role
            role_agents = registry.get_by_role(role)
            agents.extend(role_agents)
        else:
            # Get all agents
            agents = registry.all()

        # Convert to endpoint format
        endpoints = []
        for agent in agents:
            metadata = registry._metadata.get(agent.agent_id)
            if metadata and (not available or metadata.status == "active"):
                endpoints.append(AgentEndpoint(
                    agent_id=agent.agent_id,
                    service_name="agent-platform",  # Would be actual service name
                    role=agent.role,
                    capabilities=getattr(agent, "capabilities", []),
                    endpoint=getattr(agent, "endpoint", ""),
                    status=metadata.status,
                    health_check="/health"
                ))

        return endpoints

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/capabilities")
async def get_agent_capabilities():
    """Get list of all agent capabilities in the system."""
    try:
        all_agents = registry.all()
        capabilities = set()

        for agent in all_agents:
            agent_caps = getattr(agent, "capabilities", [])
            capabilities.update(agent_caps)

        return {"capabilities": list(capabilities)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/execute", response_model=AgentResponse)
async def execute_agent_task(request: AgentRequest):
    """Execute an agent task (legacy endpoint - will be deprecated)."""
    try:
        # This endpoint will be deprecated in favor of direct service calls
        # For now, return acceptance with routing information

        task_id = str(uuid.uuid4())

        # Find appropriate agents for the task
        # This would involve more sophisticated routing logic

        return AgentResponse(
            task_id=task_id,
            status="accepted",
            message=f"Task {request.task_type} queued for execution. Use direct service calls for better performance."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{task_id}")
async def get_task_status(task_id: str):
    """Get status of a specific task."""
    # TODO: Implement task status lookup from task store
    return {"task_id": task_id, "status": "running", "message": "Task status tracking not yet implemented"}


@router.get("/health/{agent_id}")
async def get_agent_health(agent_id: str):
    """Get health status of a specific agent."""
    try:
        agent = registry.get(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        metadata = registry._metadata.get(agent_id)
        if not metadata:
            raise HTTPException(status_code=404, detail="Agent metadata not found")

        return {
            "agent_id": agent_id,
            "status": metadata.status,
            "messages_processed": metadata.messages_processed,
            "last_error": metadata.last_error
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
