"""
Agent Execution Module

Consolidated from services/aesa/
Provides agent execution, orchestration, and security functionality
with PostgreSQL persistence.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from uuid import uuid4

logger = logging.getLogger(__name__)


class AgentExecutor:
    """
    Agent execution and orchestration service

    Consolidated functionality from aesa service:
    - Agent task execution and orchestration
    - Expert system integration
    - Security and policy enforcement
    - Execution state management
    - Performance monitoring
    """

    def __init__(self, store):
        self.store = store
        self.execution_timeout = 600  # 10 minutes
        self.max_concurrent_executions = 10
        self.active_executions: Dict[str, asyncio.Task] = {}
        self.expert_registry: Dict[str, Any] = {}

    async def initialize(self) -> None:
        """Initialize the executor module"""
        try:
            # Initialize expert registry (from aesa/experts/)
            await self._initialize_expert_registry()

            logger.info("Agent executor module initialized")

        except Exception as e:
            logger.error(f"Failed to initialize agent executor: {e}")
            raise

    async def _initialize_expert_registry(self) -> None:
        """Initialize expert registry from aesa/experts/"""

        # Register experts from aesa service
        experts = {
            "code_generation": {
                "class": "CodeGenExpert",
                "capabilities": ["python", "typescript", "sql"],
                "config": {"model": "gpt-4", "temperature": 0.1}
            },
            "test_generation": {
                "class": "TestGenExpert",
                "capabilities": ["unit_tests", "integration_tests"],
                "config": {"framework": "pytest", "coverage": True}
            },
            "repair_hint": {
                "class": "RepairHintExpert",
                "capabilities": ["error_analysis", "fix_suggestions"],
                "config": {"confidence_threshold": 0.7}
            },
            "schema_validation": {
                "class": "SchemaValidationExpert",
                "capabilities": ["json_schema", "data_validation"],
                "config": {"strict_mode": True}
            },
            "architect": {
                "class": "ArchitectExpert",
                "capabilities": ["system_design", "architecture_review"],
                "config": {"style": "microservices", "patterns": ["ddd", "cqrs"]}
            }
        }

        self.expert_registry.update(experts)
        logger.info(f"Registered {len(experts)} experts")

    # Agent Execution Interface
    async def execute_agent_task(self, agent_id: str, task_id: str,
                               input_data: Dict, execution_context: Optional[Dict] = None) -> Dict:
        """
        Execute an agent task

        Args:
            agent_id: ID of the agent to execute
            task_id: Unique task identifier
            input_data: Input data for the task
            execution_context: Additional context (security, constraints, etc.)

        Returns:
            Execution result with output data and metadata
        """
        try:
            execution_id = f"exec_{uuid4().hex[:12]}"

            # Check execution limits
            if len(self.active_executions) >= self.max_concurrent_executions:
                raise Exception("Maximum concurrent executions reached")

            # Create execution record
            await self.store.create_execution(
                execution_id=execution_id,
                agent_id=agent_id,
                task_id=task_id,
                input_data=input_data
            )

            # Start execution task
            execution_task = asyncio.create_task(
                self._execute_task_internal(
                    execution_id, agent_id, task_id, input_data, execution_context
                )
            )

            self.active_executions[execution_id] = execution_task

            # Wait for completion with timeout
            try:
                result = await asyncio.wait_for(execution_task, timeout=self.execution_timeout)
                return result

            except asyncio.TimeoutError:
                # Handle timeout
                await self._handle_execution_timeout(execution_id)
                raise Exception(f"Execution {execution_id} timed out")

            finally:
                # Clean up active execution
                if execution_id in self.active_executions:
                    del self.active_executions[execution_id]

        except Exception as e:
            logger.error(f"Failed to execute agent task {agent_id}/{task_id}: {e}")
            raise

    async def _execute_task_internal(self, execution_id: str, agent_id: str,
                                   task_id: str, input_data: Dict,
                                   execution_context: Optional[Dict] = None) -> Dict:
        """Internal task execution logic"""
        try:
            # Update execution state to running
            await self.store.update_execution_state(execution_id, "running")

            # Get agent definition
            agent = await self.store.get_agent_definition(agent_id)
            if not agent:
                raise ValueError(f"Agent not found: {agent_id}")

            agent_type = agent["agent_type"]
            agent_spec = agent["agent_spec"]

            # Execute based on agent type (from aesa orchestration logic)
            if agent_type in ["code_generation", "architect"]:
                result = await self._execute_expert_task(agent_type, input_data, agent_spec)
            elif agent_type == "supervisor":
                result = await self._execute_orchestration_task(input_data, agent_spec, execution_context)
            elif agent_type == "repair":
                result = await self._execute_repair_task(input_data, agent_spec)
            else:
                result = await self._execute_generic_task(agent_type, input_data, agent_spec)

            # Update execution state with results
            await self.store.update_execution_state(
                execution_id,
                "completed",
                output_data=result
            )

            logger.info(f"Execution {execution_id} completed successfully")

            return {
                "execution_id": execution_id,
                "status": "completed",
                "output_data": result,
                "metadata": {
                    "agent_id": agent_id,
                    "agent_type": agent_type,
                    "execution_time": datetime.utcnow().isoformat()
                }
            }

        except Exception as e:
            # Update execution state with error
            error_data = {"error": str(e), "timestamp": datetime.utcnow().isoformat()}
            await self.store.update_execution_state(
                execution_id,
                "failed",
                error_data=error_data
            )

            logger.error(f"Execution {execution_id} failed: {e}")
            raise

    # Execution Strategy Methods (from aesa/orchestrator/)
    async def _execute_expert_task(self, expert_type: str, input_data: Dict,
                                 agent_spec: Dict) -> Dict:
        """Execute expert-based task (from aesa/experts/)"""
        try:
            if expert_type not in self.expert_registry:
                raise ValueError(f"Unknown expert type: {expert_type}")

            expert_config = self.expert_registry[expert_type]

            # Simulate expert execution based on type
            if expert_type == "code_generation":
                return await self._simulate_code_generation(input_data, expert_config)
            elif expert_type == "architect":
                return await self._simulate_architecture_design(input_data, expert_config)
            else:
                return await self._simulate_generic_expert(input_data, expert_config)

        except Exception as e:
            logger.error(f"Expert execution failed for {expert_type}: {e}")
            raise

    async def _execute_orchestration_task(self, input_data: Dict, agent_spec: Dict,
                                        execution_context: Optional[Dict] = None) -> Dict:
        """Execute orchestration task (from aesa/orchestrator/swarm_orchestrator.py)"""
        try:
            # Simulate swarm orchestration
            sub_tasks = input_data.get("sub_tasks", [])
            orchestration_strategy = agent_spec.get("orchestration_strategy", "sequential")

            results = []

            if orchestration_strategy == "parallel":
                # Parallel execution
                tasks = [
                    self._execute_sub_task(sub_task)
                    for sub_task in sub_tasks
                ]
                results = await asyncio.gather(*tasks)

            else:
                # Sequential execution
                for sub_task in sub_tasks:
                    result = await self._execute_sub_task(sub_task)
                    results.append(result)

            return {
                "orchestration_type": "swarm",
                "strategy": orchestration_strategy,
                "sub_task_results": results,
                "total_sub_tasks": len(sub_tasks)
            }

        except Exception as e:
            logger.error(f"Orchestration execution failed: {e}")
            raise

    async def _execute_repair_task(self, input_data: Dict, agent_spec: Dict) -> Dict:
        """Execute repair task"""
        try:
            repair_type = input_data.get("repair_type", "auto_detect")
            target_execution = input_data.get("target_execution")

            # Simulate repair logic
            repair_steps = []

            if repair_type == "auto_detect":
                repair_steps = [
                    "analyze_error",
                    "identify_root_cause",
                    "apply_fix",
                    "validate_fix"
                ]
            elif repair_type == "parameter_adjustment":
                repair_steps = ["adjust_parameters", "retry_execution"]

            return {
                "repair_type": repair_type,
                "target_execution": target_execution,
                "repair_steps": repair_steps,
                "status": "completed"
            }

        except Exception as e:
            logger.error(f"Repair task execution failed: {e}")
            raise

    async def _execute_generic_task(self, agent_type: str, input_data: Dict,
                                  agent_spec: Dict) -> Dict:
        """Execute generic task for other agent types"""
        try:
            # Generic execution logic
            processing_time = input_data.get("processing_time", 1)
            await asyncio.sleep(processing_time)  # Simulate processing

            return {
                "agent_type": agent_type,
                "input_processed": True,
                "output": f"Processed by {agent_type} agent",
                "timestamp": datetime.utcnow().isoformat()
            }

        except Exception as e:
            logger.error(f"Generic task execution failed: {e}")
            raise

    # Expert Simulation Methods
    async def _simulate_code_generation(self, input_data: Dict, expert_config: Dict) -> Dict:
        """Simulate code generation expert"""
        await asyncio.sleep(2)  # Simulate processing time

        return {
            "expert_type": "code_generation",
            "language": input_data.get("language", "python"),
            "generated_code": "# Generated code placeholder",
            "confidence": 0.9,
            "model_used": expert_config["config"]["model"]
        }

    async def _simulate_architecture_design(self, input_data: Dict, expert_config: Dict) -> Dict:
        """Simulate architecture design expert"""
        await asyncio.sleep(3)  # Simulate processing time

        return {
            "expert_type": "architecture",
            "design_pattern": expert_config["config"]["patterns"][0],
            "components": ["service_a", "service_b", "database"],
            "recommendations": ["Use microservices pattern", "Implement CQRS"],
            "confidence": 0.85
        }

    async def _simulate_generic_expert(self, input_data: Dict, expert_config: Dict) -> Dict:
        """Simulate generic expert execution"""
        await asyncio.sleep(1)

        return {
            "expert_type": "generic",
            "result": "Expert processing completed",
            "capabilities_used": expert_config["capabilities"],
            "confidence": 0.8
        }

    async def _execute_sub_task(self, sub_task: Dict) -> Dict:
        """Execute a sub-task in orchestration"""
        await asyncio.sleep(0.5)  # Simulate sub-task processing

        return {
            "sub_task_id": sub_task.get("id", "unknown"),
            "result": "sub-task completed",
            "status": "success"
        }

    # Execution Management
    async def get_execution_status(self, execution_id: str) -> Optional[Dict]:
        """Get execution status"""
        try:
            return await self.store.get_execution(execution_id)

        except Exception as e:
            logger.error(f"Failed to get execution status {execution_id}: {e}")
            return None

    async def abort_execution(self, execution_id: str) -> bool:
        """Abort a running execution"""
        try:
            # Cancel the execution task if it's running
            if execution_id in self.active_executions:
                task = self.active_executions[execution_id]
                task.cancel()

                try:
                    await task
                except asyncio.CancelledError:
                    pass

                del self.active_executions[execution_id]

            # Update execution state
            await self.store.update_execution_state(
                execution_id,
                "aborted",
                error_data={"message": "Execution aborted by user"}
            )

            logger.info(f"Aborted execution {execution_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to abort execution {execution_id}: {e}")
            return False

    async def _handle_execution_timeout(self, execution_id: str) -> None:
        """Handle execution timeout"""
        try:
            await self.store.update_execution_state(
                execution_id,
                "timeout",
                error_data={"message": "Execution timed out"}
            )

            logger.warning(f"Execution {execution_id} timed out")

        except Exception as e:
            logger.error(f"Failed to handle timeout for {execution_id}: {e}")

    # Statistics and Monitoring
    async def get_execution_statistics(self) -> Dict:
        """Get execution statistics"""
        try:
            # This would query the database for statistics
            # For now, return current active executions
            return {
                "active_executions": len(self.active_executions),
                "max_concurrent": self.max_concurrent_executions,
                "registered_experts": len(self.expert_registry),
                "execution_timeout": self.execution_timeout
            }

        except Exception as e:
            logger.error(f"Failed to get execution statistics: {e}")
            return {}

    def is_healthy(self) -> bool:
        """Check if executor module is healthy"""
        return self.store is not None

    async def shutdown(self) -> None:
        """Shutdown the executor module"""
        try:
            # Cancel all active executions
            for execution_id, task in self.active_executions.items():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            self.active_executions.clear()

            logger.info("Agent executor module shutdown complete")

        except Exception as e:
            logger.error(f"Error during executor shutdown: {e}")
            raise