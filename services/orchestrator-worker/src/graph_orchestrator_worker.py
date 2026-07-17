"""
Graph Runtime Integration for Orchestrator-Worker Service

Integrates the new graph runtime with the existing orchestrator-worker infrastructure.
Maintains compatibility with existing Kafka, Redis, and storage systems.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from babyai_graph_runtime import (
    TaskState,
    TaskStatus,
    NodeResult,
    node_function,
    GraphOrchestrator,
    worker_result_reducer,
)
from babyai_graph_runtime.orchestrator import create_fan_out_graph

from bus.event_schemas import DecisionEvent, DecisionStatus
from babyai_shared.storage.context_store import ContextStore
from babyai_shared.storage.decision_status_store import DecisionStatusStore
from postgresql_orchestrator_store import PostgreSQLOrchestratorStore

logger = logging.getLogger(__name__)


class GraphOrchestratorWorker:
    """
    Graph runtime integration for orchestrator-worker service

    Integrates with existing infrastructure while using graph runtime for workflow orchestration
    """

    def __init__(
        self,
        context_store: ContextStore,
        status_store: DecisionStatusStore,
        artifact_store: Any,
        event_bus: Any,
        worker_id: Optional[str] = None,
    ):
        self.context_store = context_store
        self.status_store = status_store
        self.artifact_store = artifact_store
        self.event_bus = event_bus
        self.worker_id = worker_id or f"graph-orchestrator-{uuid4().hex[:8]}"

        # Initialize PostgreSQL store for workflow persistence
        self.orchestrator_store = None
        self._initialize_orchestrator_store()

        # Initialize graph runtime orchestrator
        self.graph_orchestrator = GraphOrchestrator(max_concurrent_nodes=5)
        self._setup_workflow_graphs()

    async def _initialize_orchestrator_store(self) -> None:
        """Initialize PostgreSQL store for workflow persistence"""
        import os
        database_url = os.getenv("ORCHESTRATOR_DATABASE_URL")
        if database_url:
            try:
                self.orchestrator_store = await PostgreSQLOrchestratorStore.create(database_url)
                logger.info(f"PostgreSQL orchestrator store initialized for worker: {self.worker_id}")
            except Exception as e:
                logger.warning(f"Failed to initialize PostgreSQL store: {e}. Workflow persistence disabled.")
        else:
            logger.info("ORCHESTRATOR_DATABASE_URL not set. Workflow persistence disabled.")

    def _setup_workflow_graphs(self) -> None:
        """Set up the graph runtime workflows"""

        # Create episode processing workflow
        episode_workflow = create_fan_out_graph(
            orchestrator_node=self._create_episode_orchestrator(),
            worker_nodes=[
                self._create_task_loader_worker(),
                self._create_context_creator_worker(),
                self._create_truth_loader_worker()
            ],
            reducer_node=self._create_episode_executor(),
            worker_reducer=worker_result_reducer(expected_workers=3)
        )

        # Add result publishing step
        episode_workflow.add_node(self._create_result_publisher())
        episode_workflow.add_exit_point("result_publisher")

        # Connect executor to publisher
        from babyai_graph_runtime.edges import conditional_edge
        executor_to_publisher_edge = conditional_edge(
            condition=lambda state: state.get("episode_status") == "completed",
            true_nodes=["result_publisher"],
            name="executor_to_publisher"
        )
        episode_workflow.add_edge("episode_executor", executor_to_publisher_edge)

        # Register workflow
        self.graph_orchestrator.register_graph(episode_workflow)

        logger.info(f"Graph workflows initialized for worker: {self.worker_id}")

    def _create_episode_orchestrator(self):
        """Create the episode orchestrator node"""

        @node_function("episode_orchestrator")
        async def episode_orchestrator(state: TaskState) -> NodeResult:
            """Main orchestrator that distributes episode processing work"""

            # Get event data from state
            event_data = state.get("decision_event")
            if not event_data:
                return NodeResult(
                    errors=[{"node_name": "episode_orchestrator", "message": "Missing decision event"}]
                )

            event = DecisionEvent(**event_data)

            # Create work partitions for parallel processing
            work_partitions = [
                {
                    "partition_id": "task_loading",
                    "worker_type": "task_loader",
                    "task_ref": event.task_ref,
                    "decision_id": event.decision_id
                },
                {
                    "partition_id": "context_setup",
                    "worker_type": "context_creator",
                    "context_id": event.context_id,
                    "decision_id": event.decision_id
                },
                {
                    "partition_id": "truth_loading",
                    "worker_type": "truth_loader",
                    "truth_pack_ref": event.truth_pack_ref,
                    "decision_id": event.decision_id
                }
            ]

            logger.info(f"Orchestrator: Created {len(work_partitions)} work partitions for decision {event.decision_id}")

            return NodeResult(
                updates={
                    "work_partitions": work_partitions,
                    "orchestration_status": "distributed",
                    "decision_event": event_data
                },
                next_nodes=["task_loader_worker", "context_creator_worker", "truth_loader_worker"],
                metadata={
                    "pattern": "orchestrator",
                    "decision_id": event.decision_id,
                    "partitions": len(work_partitions)
                }
            )

        return episode_orchestrator

    def _create_task_loader_worker(self):
        """Create task loader worker node"""

        @node_function("task_loader_worker", retry_count=2)
        async def task_loader_worker(state: TaskState) -> NodeResult:
            """Worker that loads task specifications using existing infrastructure"""

            # Get work partition
            partitions = state.get("work_partitions", [])
            task_partition = next((p for p in partitions if p.get("worker_type") == "task_loader"), None)

            if not task_partition:
                return NodeResult(updates={"worker_result": "no_task_work"})

            task_ref = task_partition["task_ref"]
            decision_id = task_partition["decision_id"]

            try:
                # Use existing task loading logic from orchestrator_worker
                import orchestrator_worker as ow
                task = ow.OrchestratorWorker._load_task(self, task_ref)

                logger.info(f"Task Loader: Loaded task for decision {decision_id}")

                return NodeResult(
                    updates={
                        "task_data": task,
                        "task_ref": task_ref,
                        "worker_type": "task_loader",
                        "partition_id": task_partition["partition_id"]
                    },
                    metadata={
                        "worker": "task_loader",
                        "decision_id": decision_id
                    }
                )

            except Exception as e:
                logger.error(f"Task loader failed for decision {decision_id}: {e}")
                return NodeResult(
                    errors=[{"node_name": "task_loader_worker", "message": str(e)}]
                )

        return task_loader_worker

    def _create_context_creator_worker(self):
        """Create context creator worker node"""

        @node_function("context_creator_worker", retry_count=2)
        async def context_creator_worker(state: TaskState) -> NodeResult:
            """Worker that sets up execution context using existing infrastructure"""

            # Get work partition
            partitions = state.get("work_partitions", [])
            context_partition = next((p for p in partitions if p.get("worker_type") == "context_creator"), None)

            if not context_partition:
                return NodeResult(updates={"worker_result": "no_context_work"})

            context_id = context_partition["context_id"]
            decision_id = context_partition["decision_id"]

            try:
                # Use existing context creation logic
                import orchestrator_worker as ow
                context = ow.OrchestratorWorker._get_or_create_context(self, context_id)

                logger.info(f"Context Creator: Created context for decision {decision_id}")

                return NodeResult(
                    updates={
                        "execution_context": context,
                        "context_id": context_id,
                        "worker_type": "context_creator",
                        "partition_id": context_partition["partition_id"]
                    },
                    metadata={
                        "worker": "context_creator",
                        "decision_id": decision_id
                    }
                )

            except Exception as e:
                logger.error(f"Context creator failed for decision {decision_id}: {e}")
                return NodeResult(
                    errors=[{"node_name": "context_creator_worker", "message": str(e)}]
                )

        return context_creator_worker

    def _create_truth_loader_worker(self):
        """Create truth loader worker node"""

        @node_function("truth_loader_worker", retry_count=2)
        async def truth_loader_worker(state: TaskState) -> NodeResult:
            """Worker that loads truth pack using existing infrastructure"""

            # Get work partition
            partitions = state.get("work_partitions", [])
            truth_partition = next((p for p in partitions if p.get("worker_type") == "truth_loader"), None)

            if not truth_partition:
                return NodeResult(updates={"worker_result": "no_truth_work"})

            truth_pack_ref = truth_partition["truth_pack_ref"]
            decision_id = truth_partition["decision_id"]

            try:
                # Use existing truth pack loading logic
                import orchestrator_worker as ow
                truth_pack = ow.load_truth_pack(truth_pack_ref)

                logger.info(f"Truth Loader: Loaded truth pack for decision {decision_id}")

                return NodeResult(
                    updates={
                        "truth_pack": truth_pack,
                        "truth_pack_ref": truth_pack_ref,
                        "worker_type": "truth_loader",
                        "partition_id": truth_partition["partition_id"]
                    },
                    metadata={
                        "worker": "truth_loader",
                        "decision_id": decision_id
                    }
                )

            except Exception as e:
                logger.error(f"Truth loader failed for decision {decision_id}: {e}")
                return NodeResult(
                    errors=[{"node_name": "truth_loader_worker", "message": str(e)}]
                )

        return truth_loader_worker

    def _create_episode_executor(self):
        """Create episode executor node"""

        @node_function("episode_executor")
        async def episode_executor(state: TaskState) -> NodeResult:
            """Execute episode using existing run_episode logic with prepared data"""

            # Get aggregated worker results
            worker_results = state.get("worker_results", [])
            event_data = state.get("decision_event", {})

            # Extract data from worker results
            task_data = None
            execution_context = None
            truth_pack = None

            for result in worker_results:
                if result.get("worker_type") == "task_loader":
                    task_data = result.get("task_data")
                elif result.get("worker_type") == "context_creator":
                    execution_context = result.get("execution_context")
                elif result.get("worker_type") == "truth_loader":
                    truth_pack = result.get("truth_pack")

            if not all([task_data, execution_context, truth_pack]):
                missing = []
                if not task_data: missing.append("task_data")
                if not execution_context: missing.append("execution_context")
                if not truth_pack: missing.append("truth_pack")

                return NodeResult(
                    errors=[{
                        "node_name": "episode_executor",
                        "message": f"Missing required data: {', '.join(missing)}"
                    }]
                )

            decision_id = event_data.get("decision_id", "unknown")
            event = DecisionEvent(**event_data)

            try:
                # Use existing episode execution logic
                import orchestrator_worker as ow

                # Extract metadata for episode execution
                metadata = dict(event.metadata) if isinstance(event.metadata, dict) else {}
                metadata.setdefault("decision_id", decision_id)
                metadata.setdefault("context_id", event.context_id)

                # Load skill bundle
                skill_bundle = self._resolve_skill_bundle(event=event, metadata=metadata, task=task_data)

                # Execute episode using existing logic
                episode = ow.run_episode(task_data, truth_pack, knobs=metadata, skill_bundle=skill_bundle)

                # Process results
                decision = episode.decision or {}
                execution_result = {
                    "decision_id": decision_id,
                    "status": "completed",
                    "decision": decision,
                    "episode_telemetry": episode.telemetry,
                    "eval_result": episode.eval_result or {}
                }

                logger.info(f"Episode Executor: Completed decision {decision_id}")

                return NodeResult(
                    updates={
                        "execution_result": execution_result,
                        "episode_status": "completed",
                        "decision": decision
                    },
                    next_nodes=["result_publisher"],
                    metadata={
                        "pattern": "executor",
                        "decision_id": decision_id,
                        "tokens_used": episode.telemetry.get("tokens_used", 0)
                    }
                )

            except Exception as e:
                logger.error(f"Episode executor failed for decision {decision_id}: {e}")
                return NodeResult(
                    errors=[{"node_name": "episode_executor", "message": str(e)}]
                )

        return episode_executor

    def _create_result_publisher(self):
        """Create result publisher node"""

        @node_function("result_publisher")
        async def result_publisher(state: TaskState) -> NodeResult:
            """Publish results using existing event bus infrastructure"""

            execution_result = state.get("execution_result")
            event_data = state.get("decision_event", {})

            if not execution_result:
                return NodeResult(
                    errors=[{"node_name": "result_publisher", "message": "No execution result to publish"}]
                )

            event = DecisionEvent(**event_data)
            decision_id = event.decision_id

            try:
                # Use existing publishing logic from orchestrator worker
                import orchestrator_worker as ow

                # Publish completion status
                self._publish_status(event, DecisionStatus.COMPLETED)

                # Publish evaluation results if available
                eval_result = execution_result.get("eval_result", {})
                if eval_result:
                    self._publish_eval_result(event=event, **eval_result)

                logger.info(f"Result Publisher: Published results for decision {decision_id}")

                return NodeResult(
                    updates={
                        "publication_status": "success",
                        "workflow_status": "completed"
                    },
                    metadata={
                        "pattern": "publisher",
                        "decision_id": decision_id,
                        "final": True
                    }
                )

            except Exception as e:
                logger.error(f"Result publisher failed for decision {decision_id}: {e}")
                return NodeResult(
                    errors=[{"node_name": "result_publisher", "message": str(e)}]
                )

        return result_publisher

    def _resolve_skill_bundle(self, event, metadata, task):
        """Use existing skill bundle resolution logic"""
        try:
            import orchestrator_worker as ow
            return ow.OrchestratorWorker._resolve_skill_bundle(self, event=event, metadata=metadata, task=task)
        except Exception:
            return None

    def _publish_status(self, event, status, **kwargs):
        """Use existing status publishing logic"""
        try:
            import orchestrator_worker as ow
            return ow.OrchestratorWorker._publish_status(self, event, status, **kwargs)
        except Exception as e:
            logger.warning(f"Failed to publish status: {e}")

    def _publish_eval_result(self, event, **kwargs):
        """Use existing eval result publishing logic"""
        try:
            import orchestrator_worker as ow
            return ow.OrchestratorWorker._publish_eval_result(self, event=event, **kwargs)
        except Exception as e:
            logger.warning(f"Failed to publish eval result: {e}")

    async def process_decision_event(self, event: DecisionEvent) -> TaskState:
        """
        Process a decision event using graph runtime workflow

        This is the main entry point that replaces the legacy _process_episode method
        """

        # Create initial workflow state
        initial_state = TaskState(
            task_id=f"workflow_{event.decision_id}",
            data={
                "decision_event": event.to_dict(),
                "workflow_type": "episode_processing"
            }
        )

        # Execute the graph workflow
        result_state = await self.graph_orchestrator.execute_graph(
            graph_name=f"fanout_{self._create_episode_orchestrator().name}",
            initial_state=initial_state,
            timeout_seconds=300.0  # 5 minute timeout
        )

        logger.info(f"Graph workflow completed for decision {event.decision_id}: {result_state.status}")

        return result_state