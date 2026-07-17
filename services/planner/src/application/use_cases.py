from __future__ import annotations

import logging
from typing import Any, Mapping

from planner.domain import build_decision_requested, build_task_spec, parse_intent, parse_ready

from .ports import DecisionRequestedPublisher, DlqPublisher, TaskSpecStore

_log = logging.getLogger(__name__)


class PlannerService:
    def __init__(
        self,
        *,
        task_store: TaskSpecStore,
        decision_requested_publisher: DecisionRequestedPublisher,
        dlq_publisher: DlqPublisher,
    ) -> None:
        self._task_store = task_store
        self._decision_requested_publisher = decision_requested_publisher
        self._dlq_publisher = dlq_publisher
        self._intent_cache: dict[str, Any] = {}
        # Lazy-init memory context — never blocks constructor
        self._memory_ctx: Any = None
        self._memory_ctx_loaded = False

    def handle_intent(self, payload: Mapping[str, Any]) -> None:
        try:
            intent = parse_intent(payload)
            self._intent_cache[intent.decision_id] = intent
        except Exception as exc:
            self._dlq_publisher.publish_dlq(
                reason_code="INTENT_INVALID",
                message=str(exc),
                payload={"source": "decision.intent", "raw": dict(payload or {})},
            )

    def handle_ready(self, payload: Mapping[str, Any]) -> None:
        try:
            ready = parse_ready(payload)
            intent = self._intent_cache.get(ready.decision_id)
            if intent is None:
                raise ValueError(f"intent not found for decision_id={ready.decision_id}")
            task_spec = build_task_spec(intent=intent, ready=ready)
            task_ref = self._task_store.store(task_spec=task_spec)
            decision_requested = build_decision_requested(intent=intent, ready=ready, task_ref=task_ref)

            # Pre-run memory retrieval — fail-open, never blocks episode start
            try:
                memory_ctx = self._get_memory_ctx()
                if memory_ctx is not None:
                    memory_data = memory_ctx.retrieve_for_episode(
                        scenario=str(intent.policy_preset),
                        agent_context={"context_id": str(intent.context_id)},
                    )
                    if memory_data.get("total_retrieved", 0) > 0:
                        memory_text = memory_ctx.format_for_context(memory_data)
                        metadata = dict(decision_requested.get("metadata") or {})
                        metadata["memory_context"] = memory_text
                        decision_requested = dict(decision_requested)
                        decision_requested["metadata"] = metadata
                        _log.info(
                            "Memory context injected: %d memories for decision_id=%s",
                            memory_data["total_retrieved"],
                            intent.decision_id,
                        )
            except Exception as exc:
                _log.warning("Memory context retrieval failed (continuing): %s", exc)

            self._decision_requested_publisher.publish(decision_requested)
        except Exception as exc:
            self._dlq_publisher.publish_dlq(
                reason_code="READY_INVALID",
                message=str(exc),
                payload={"source": "decision.truthpack.ready", "raw": dict(payload or {})},
            )

    def _get_memory_ctx(self) -> Any:
        if not self._memory_ctx_loaded:
            self._memory_ctx_loaded = True
            try:
                from planner.memory_context import PlannerMemoryContext
                self._memory_ctx = PlannerMemoryContext()
            except Exception as exc:
                _log.debug("PlannerMemoryContext unavailable: %s", exc)
        return self._memory_ctx
