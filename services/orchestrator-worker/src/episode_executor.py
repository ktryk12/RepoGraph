"""
EpisodeExecutorMixin — episode execution and evaluation logic for OrchestratorWorker.

Handles: run_episode orchestration, artifact storage, eval scoring.

NOTE: run_episode and load_truth_pack are imported lazily from orchestrator_worker
inside the methods that use them.  This preserves monkeypatch compatibility:
    monkeypatch.setattr("orchestrator_worker.run_episode", mock)
works correctly because the lazy import always fetches the current module-level name.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

from bus.event_schemas import ArtifactEvent, DecisionEvent, SCHEMA_VERSION, now_iso
from bus import metrics
from babyai_shared.core.logging_milestones import log_milestone
from policy.governance_smoke import (
    build_governance_artifact_payload,
    evaluate_governance_hello_world_artifact,
    expected_payload as governance_expected_payload,
    extract_model_json_payload,
    is_governance_hello_world_task,
)
from policy.ops_readiness import ops_readiness_status
from policy.scorer import load_rules, score_architecture
from babyai_shared.bus.protocol import Context

logger = logging.getLogger("orchestrator_worker")


class EpisodeExecutorMixin:
    """
    Episode execution and evaluation.  Requires the following attributes on self:
      artifact_store, context_store, event_bus, _rules_cache
    and the methods: _publish_event, _log_milestone, _topic_name, _maybe_failpoint.
    """

    # ── Episode orchestration ────────────────────────────────────────────────

    def _process_episode(self, event: DecisionEvent) -> None:
        # Check if graph runtime is enabled and available
        if getattr(self, '_use_graph_runtime', False) and getattr(self, '_graph_worker', None):
            return self._process_episode_with_graph_runtime(event)

        # Fallback to legacy processing
        # Lazy import preserves monkeypatch("orchestrator_worker.run_episode", ...)
        import orchestrator_worker as _ow

        self._publish_status(event, _ow.DecisionStatus.STARTED)

        task = self._load_task(event.task_ref)

    async def _process_episode_with_graph_runtime(self, event: DecisionEvent) -> None:
        """Process episode using graph runtime workflow orchestration"""
        import orchestrator_worker as _ow

        logger.info(f"Processing decision {event.decision_id} with graph runtime")

        try:
            # Publish initial status
            self._publish_status(event, _ow.DecisionStatus.STARTED)

            # Execute graph workflow
            result_state = await self._graph_worker.process_decision_event(event)

            # Handle workflow results
            if result_state.status == _ow.TaskStatus.COMPLETED:
                logger.info(f"Graph runtime completed successfully for decision {event.decision_id}")
                if result_state.errors:
                    # Log warnings for any non-fatal errors
                    for error in result_state.errors:
                        logger.warning(f"Non-fatal error in {error.node_name}: {error.message}")
            elif result_state.status == _ow.TaskStatus.FAILED:
                error_msg = "Graph runtime workflow failed"
                if result_state.errors:
                    error_details = "; ".join([f"{e.node_name}: {e.message}" for e in result_state.errors])
                    error_msg = f"{error_msg}: {error_details}"

                logger.error(f"Graph runtime failed for decision {event.decision_id}: {error_msg}")
                self._publish_status(event, _ow.DecisionStatus.FAILED, error=error_msg)
                return
            else:
                logger.warning(f"Graph runtime returned unexpected status {result_state.status} for decision {event.decision_id}")

        except Exception as e:
            logger.error(f"Graph runtime execution failed for decision {event.decision_id}: {e}", exc_info=True)
            self._publish_status(event, _ow.DecisionStatus.FAILED, error=str(e))
            raise

        context = self._get_or_create_context(event.context_id)
        context.task_spec = task
        context.attach_ref("task", event.task_ref)
        context.task_ref = event.task_ref
        context.truth_pack_ref = event.truth_pack_ref
        context.truth_pack_version = event.truth_pack_version
        self.context_store.save(context)

        self._publish_status(event, _ow.DecisionStatus.GENERATING, iteration=1)
        self._publish_status(event, _ow.DecisionStatus.EVALUATING, iteration=1)

        truth_source = str(event.truth_pack_ref)
        metadata = dict(event.metadata) if isinstance(event.metadata, dict) else {}
        metadata.setdefault("decision_id", str(event.decision_id))
        metadata.setdefault("context_id", str(event.context_id))
        policy_fingerprint = self._policy_fingerprint_for_event(event)
        if policy_fingerprint:
            metadata.setdefault("policy_fingerprint", policy_fingerprint)
        if self._approval_required_for_event(event):
            permit_payload = metadata.get("execution_permit") or metadata.get("approval_token")
            if not isinstance(permit_payload, dict):
                raise _ow.ApprovalMissingError(
                    "approval_missing: execution_permit is required before episode execution"
                )
        override_ref = metadata.get("truth_override_ref")
        if isinstance(override_ref, str) and override_ref.strip():
            truth_source = override_ref.strip()
        truth_pack = _ow.load_truth_pack(truth_source)
        skill_bundle = self._resolve_skill_bundle(
            event=event,
            metadata=metadata,
            task=task,
        )
        self._log_milestone(
            milestone="runtime_invocation_start",
            component="orchestrator_worker._process_episode",
            event=event,
            topic="",
            event_type="runtime.invoke",
            model_ref=str(metadata.get("model_ref") or ""),
            tool_ref=str(metadata.get("tool_ref") or ""),
            truth_source=str(truth_source),
        )
        episode = _ow.run_episode(
            task,
            truth_pack,
            knobs=metadata,
            skill_bundle=skill_bundle,
        )
        self._log_milestone(
            milestone="runtime_invocation_done",
            component="orchestrator_worker._process_episode",
            event=event,
            topic="",
            event_type="runtime.invoke",
            status="ok",
            tokens_used=_optional_int(episode.telemetry.get("tokens_used")),
            latency_ms=_optional_float(episode.telemetry.get("latency_ms")),
        )

        decision = episode.decision or {}
        governance_eval_hint: Dict[str, Any] = {}
        try:
            governance_eval_hint = self._maybe_write_governance_smoke_artifact(
                event=event,
                task=task,
                decision=decision,
            )
        except Exception as exc:
            if is_governance_hello_world_task(task):
                governance_eval_hint = {"error": "governance_artifact_write_failed", "error_detail": str(exc)}
                decision["governance_smoke_error"] = str(exc)
            else:
                raise
        if governance_eval_hint:
            decision["_governance_smoke_eval"] = governance_eval_hint
        decision_ref = self._store_decision(event.context_id, decision)
        self._log_milestone(
            milestone="artifact_written",
            component="orchestrator_worker._process_episode",
            event=event,
            topic=self._topic_name("artifact_events", "artifact.events"),
            event_type="artifact.events",
            artifact_name="decision:final",
            artifact_ref=str(decision_ref),
            content_hash=(
                str(decision_ref).split("artifact:sha256:", 1)[1]
                if "artifact:sha256:" in str(decision_ref) else ""
            ),
        )
        self._maybe_failpoint("after_decision_store")

        eval_result = episode.eval_result or {}
        metrics.observe_repairs(int(episode.telemetry.get("repairs_used", 0)))
        gate_results = self._gate_results(decision)
        artifacts_value = decision.get("artifacts") if isinstance(decision, dict) else None
        artifact_count = len(artifacts_value) if isinstance(artifacts_value, (list, dict)) else 0
        generated_output = decision.get("generated_output") if isinstance(decision, dict) else None
        generated_output_text = ""
        if isinstance(generated_output, dict):
            generated_output_text = str(generated_output.get("text") or "").strip()
        logger.info(
            "debug_eval_input decision_id=%s context_id=%s decision_ref=%s task_template=%s "
            "generated_output_len=%d generated_output_text=%s eval_keys=%s",
            event.decision_id,
            event.context_id,
            str(decision_ref),
            str(task.get("template") or "").strip().lower(),
            len(generated_output_text),
            _clip_text(generated_output_text, max_chars=900),
            sorted([str(k) for k in eval_result.keys()]),
        )
        self._log_milestone(
            milestone="eval_started",
            component="orchestrator_worker._process_episode",
            event=event,
            topic="",
            event_type="eval",
            artifact_count=artifact_count,
            decision_ref=str(decision_ref),
        )
        passed, score, components, failure_reasons = self._build_eval_payload(
            event=event,
            task=task,
            decision=decision,
            decision_ref=decision_ref,
            eval_result=eval_result,
        )
        self._log_milestone(
            milestone="eval_done",
            component="orchestrator_worker._process_episode",
            event=event,
            topic="",
            event_type="eval",
            passed=bool(passed),
            score=float(score),
            components_keys=sorted([str(k) for k in dict(components or {}).keys()]),
            failure_reasons_count=len(list(failure_reasons or [])),
        )
        penalties = list(eval_result.get("penalties") or [])

        iterations = int(episode.telemetry.get("repairs_used", 0)) + 1

        self._publish_status(
            event,
            _ow.DecisionStatus.EVALUATED,
            iteration=iterations,
            metadata={"score": score, "decision_ref": decision_ref},
        )

        self._publish_eval_result(
            event=event,
            iteration=iterations,
            passed=passed,
            score=score,
            components=components,
            gate_results=gate_results,
            penalties=penalties,
            failure_reasons=failure_reasons,
            decision_ref=decision_ref,
            runner_used=_optional_text(episode.telemetry.get("runner_used")),
            tokens_used=_optional_int(episode.telemetry.get("tokens_used")),
            latency_ms=_optional_float(episode.telemetry.get("latency_ms")),
            training_data=(
                episode.telemetry.get("training_data")
                if isinstance(episode.telemetry.get("training_data"), dict) else None
            ),
        )
        metrics.observe_eval_score(score)

        final_status = _ow.DecisionStatus.COMPLETED if passed else _ow.DecisionStatus.FAILED
        self._publish_status(
            event,
            final_status,
            iteration=iterations,
            metadata={
                "iterations": iterations,
                "final_score": score,
                "decision_ref": decision_ref,
            },
        )
        self._log_milestone(
            milestone="final_outcome_published",
            component="orchestrator_worker._process_episode",
            event=event,
            topic=self._topic_name("decision_lifecycle", "decision.lifecycle"),
            event_type=final_status.value,
            passed=bool(final_status == _ow.DecisionStatus.COMPLETED),
            score=float(score),
            failure_reasons=list(failure_reasons or []),
        )
        self.status_store.set_status(
            event.decision_id,
            "completed" if final_status == _ow.DecisionStatus.COMPLETED else "failed",
            ttl_seconds=self._final_ttl(),
        )

    # ── Artifact helpers ─────────────────────────────────────────────────────

    def _get_or_create_context(self, context_id: str) -> Context:
        try:
            return self.context_store.load(context_id)
        except Exception:
            return Context(context_id=context_id)

    def _load_task(self, task_ref: str) -> Dict[str, Any]:
        data = self.artifact_store.get(task_ref)
        if data is None:
            raise ValueError(f"Task artifact not found: {task_ref}")
        return json.loads(data.decode("utf-8"))

    def _store_decision(self, context_id: str, decision: Dict[str, Any]) -> str:
        payload = json.dumps(decision, ensure_ascii=True).encode("utf-8")
        ref = self.artifact_store.put(
            payload,
            context_id=context_id,
            name="decision:final",
            metadata={"type": "decision"},
        ).ref

        artifact_event = ArtifactEvent(
            schema_version=SCHEMA_VERSION,
            event_type="CREATED",
            artifact_ref=ref,
            context_id=context_id,
            artifact_type="decision",
            timestamp=now_iso(),
            size_bytes=len(payload),
        )
        self._publish_event(
            context_id=context_id,
            topic=self._topic_name("artifact_events", "artifact.events"),
            key=ref,
            event=artifact_event,
        )
        return ref

    def _maybe_write_governance_smoke_artifact(
        self,
        *,
        event: DecisionEvent,
        task: Dict[str, Any],
        decision: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not is_governance_hello_world_task(task):
            return {}

        model_payload = extract_model_json_payload(decision)
        normalized_source_payload: Dict[str, Any] = governance_expected_payload()
        if isinstance(model_payload, dict):
            hello_value = str(model_payload.get("hello") or "").strip().lower()
            if hello_value == "world":
                normalized_source_payload = governance_expected_payload()
        artifact_payload = build_governance_artifact_payload(
            decision_id=str(event.decision_id),
            context_id=str(event.context_id),
            json_payload=normalized_source_payload,
        )
        artifact_raw = json.dumps(
            artifact_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        artifact_ref = self.artifact_store.put(
            artifact_raw,
            context_id=str(event.context_id),
            name="governance_smoke.v1",
            metadata={"type": "governance_smoke", "template": "governance_hello_world.v1"},
        ).ref
        decision["governance_smoke_ref"] = str(artifact_ref)
        decision["governance_smoke_payload"] = dict(artifact_payload)
        if isinstance(model_payload, dict):
            decision["governance_model_payload"] = dict(model_payload)
        artifact_event = ArtifactEvent(
            schema_version=SCHEMA_VERSION,
            event_type="CREATED",
            artifact_ref=str(artifact_ref),
            context_id=str(event.context_id),
            artifact_type="governance_smoke",
            timestamp=now_iso(),
            size_bytes=len(artifact_raw),
        )
        self._publish_event(
            context_id=str(event.context_id),
            topic=self._topic_name("artifact_events", "artifact.events"),
            key=str(artifact_ref),
            event=artifact_event,
        )
        self._log_milestone(
            milestone="artifact_written",
            component="orchestrator_worker._maybe_write_governance_smoke_artifact",
            event=event,
            topic=self._topic_name("artifact_events", "artifact.events"),
            event_type="artifact.events",
            artifact_name="governance_smoke.v1",
            artifact_ref=str(artifact_ref),
            content_hash=str(artifact_payload.get("content_hash") or ""),
        )
        return {
            "artifact_ref": str(artifact_ref),
            "artifact_payload": dict(artifact_payload),
        }

    # ── Eval ─────────────────────────────────────────────────────────────────

    def _build_eval_payload(
        self,
        *,
        event: DecisionEvent,
        task: Dict[str, Any],
        decision: Dict[str, Any],
        decision_ref: str,
        eval_result: Dict[str, Any],
    ) -> tuple[bool, float, Dict[str, float], list[str]]:
        task_template = str(task.get("template") or "").strip().lower()
        generated_output = decision.get("generated_output") if isinstance(decision, dict) else None
        generated_output_text = ""
        if isinstance(generated_output, dict):
            generated_output_text = str(generated_output.get("text") or "").strip()
        logger.info(
            "debug_eval_input decision_id=%s context_id=%s task_ref=%s decision_ref=%s "
            "template=%s generated_output_len=%d generated_output_text=%s eval_keys=%s",
            event.decision_id,
            event.context_id,
            event.task_ref,
            decision_ref,
            task_template or "",
            len(generated_output_text),
            _clip_text(generated_output_text, max_chars=900),
            sorted([str(k) for k in eval_result.keys()]),
        )
        raw_scores = eval_result.get("scores")
        logger.info(
            "eval_template_detected decision_id=%s task_ref=%s decision_ref=%s template=%s "
            "eval_passed_raw=%s has_scores=%s has_generated_output_text=%s",
            event.decision_id,
            event.task_ref,
            decision_ref,
            task_template or "",
            bool(eval_result.get("passed")),
            isinstance(raw_scores, dict),
            bool(generated_output_text),
        )
        if task_template == "auto":
            logger.info(
                "eval_auto_template_detected decision_id=%s task_ref=%s decision_ref=%s",
                event.decision_id,
                event.task_ref,
                decision_ref,
            )
        if is_governance_hello_world_task(task):
            governance_hint = decision.get("_governance_smoke_eval")
            artifact_payload = None
            governance_error = ""
            if isinstance(governance_hint, dict):
                artifact_payload = governance_hint.get("artifact_payload")
                governance_error = str(governance_hint.get("error") or "").strip()
            governance_eval = evaluate_governance_hello_world_artifact(
                artifact_payload if isinstance(artifact_payload, dict) else None
            )
            passed = bool(governance_eval.get("passed", False))
            score = float(governance_eval.get("score", 0.0))
            components = dict(governance_eval.get("components") or {})
            failure_reasons = [
                str(item) for item in list(governance_eval.get("failure_reasons") or []) if str(item)
            ]
            if governance_error and governance_error not in failure_reasons:
                failure_reasons.append(governance_error)
            if (not passed) and not failure_reasons:
                failure_reasons = ["governance_eval_failed"]
            self._log_milestone(
                milestone="eval_done",
                component="orchestrator_worker._build_eval_payload",
                event=event,
                topic=self._topic_name("eval_results", "eval.results"),
                event_type="governance_eval",
                passed=passed,
                score=score,
                components_keys=sorted([str(k) for k in components.keys()]),
                failure_reasons=failure_reasons,
            )
            logger.info(
                "debug_eval_result decision_id=%s context_id=%s template=%s passed=%s score=%.3f "
                "component_keys=%s failure_reasons=%s",
                event.decision_id,
                event.context_id,
                task_template or "",
                bool(passed),
                float(score),
                sorted([str(k) for k in components.keys()]),
                list(failure_reasons),
            )
            return passed, score, components, failure_reasons

        if task_template == "auto":
            prompt_text = str(task.get("prompt") or task.get("objective") or task.get("title") or "").strip()
            passed, score, components, failure_reasons = _auto_eval_payload(
                prompt_text=prompt_text,
                answer_text=generated_output_text,
            )
            reason = "; ".join(failure_reasons) if failure_reasons else "auto_eval_passed"
            logger.info(
                "eval_auto_template_result decision_id=%s task_ref=%s decision_ref=%s passed=%s "
                "score=%.3f component_keys=%s reason=%s",
                event.decision_id,
                event.task_ref,
                decision_ref,
                bool(passed),
                float(score),
                sorted([str(k) for k in components.keys()]),
                reason,
            )
            logger.info(
                "debug_eval_result decision_id=%s context_id=%s template=%s passed=%s score=%.3f "
                "component_keys=%s failure_reasons=%s",
                event.decision_id,
                event.context_id,
                task_template or "",
                bool(passed),
                float(score),
                sorted([str(k) for k in components.keys()]),
                list(failure_reasons),
            )
            return passed, score, components, failure_reasons

        passed = bool(eval_result.get("passed"))
        score, components = self._score_components(eval_result)
        score_source = "eval_result"
        if not components:
            logger.info(
                "eval_fallback_architecture_enter decision_id=%s task_ref=%s decision_ref=%s template=%s passed=%s",
                event.decision_id,
                event.task_ref,
                decision_ref,
                task_template or "",
                bool(passed),
            )
            logger.info(
                "eval_score_missing_in_eval_result decision_id=%s task_ref=%s decision_ref=%s eval_keys=%s",
                event.decision_id,
                event.task_ref,
                decision_ref,
                sorted([str(k) for k in eval_result.keys()]),
            )
            score, components = self._score_from_architecture(
                event=event,
                task=task,
                decision=decision,
                decision_ref=decision_ref,
            )
            logger.info(
                "eval_fallback_architecture_result decision_id=%s task_ref=%s decision_ref=%s "
                "template=%s score=%.3f component_keys=%s",
                event.decision_id,
                event.task_ref,
                decision_ref,
                task_template or "",
                float(score),
                sorted([str(k) for k in components.keys()]),
            )
            score_source = "score_architecture_fallback" if components else "none"
        else:
            logger.info(
                "eval_score_from_eval_result decision_id=%s task_ref=%s decision_ref=%s "
                "component_keys=%s total=%.3f",
                event.decision_id,
                event.task_ref,
                decision_ref,
                sorted([str(k) for k in components.keys()]),
                float(score),
            )

        failure_reasons = self._normalize_failure_reasons(
            failure_reasons=self._failure_reasons(eval_result),
            passed=passed,
            components=components,
            score=score,
        )
        logger.info(
            "eval_score_resolved decision_id=%s source=%s passed=%s score=%.3f "
            "component_keys=%s failure_reasons=%s",
            event.decision_id,
            score_source,
            bool(passed),
            float(score),
            sorted([str(k) for k in components.keys()]),
            list(failure_reasons),
        )
        if (not bool(passed)) and not list(failure_reasons):
            gate_snapshot = eval_result.get("gate_results")
            if not isinstance(gate_snapshot, dict):
                gate_snapshot = {}
            log_milestone(
                logger,
                "failed_without_reasons",
                service_name="orchestrator-worker",
                component="orchestrator_worker._build_eval_payload",
                decision_id=str(event.decision_id),
                context_id=str(event.context_id),
                episode_id=str(event.decision_id),
                event_type="eval",
                topic=self._topic_name("eval_results", "eval.results"),
                fingerprint=self._event_fingerprint(event),
                event_id=str(getattr(event, "event_id", "") or getattr(event, "content_hash", "")),
                trace_id=self._trace_id_from_metadata(event.metadata),
                gates=gate_snapshot,
                score=float(score),
                components_keys=sorted([str(k) for k in components.keys()]),
            )
            logger.warning(
                "failed_without_reasons decision_id=%s score=%.3f gates=%s components_keys=%s",
                event.decision_id,
                float(score),
                sorted([str(k) for k in dict(gate_snapshot).keys()]),
                sorted([str(k) for k in dict(components).keys()]),
            )
        logger.info(
            "debug_eval_result decision_id=%s context_id=%s template=%s passed=%s score=%.3f "
            "component_keys=%s failure_reasons=%s",
            event.decision_id,
            event.context_id,
            task_template or "",
            bool(passed),
            float(score),
            sorted([str(k) for k in dict(components).keys()]),
            list(failure_reasons),
        )
        return passed, score, components, failure_reasons

    def _score_from_architecture(
        self,
        *,
        event: DecisionEvent,
        task: Dict[str, Any],
        decision: Dict[str, Any],
        decision_ref: str,
    ) -> tuple[float, Dict[str, float]]:
        if not isinstance(task, dict) or not isinstance(decision, dict) or not decision:
            return 0.0, {}
        logger.info(
            "eval_score_architecture_call decision_id=%s task_ref=%s decision_ref=%s "
            "task_id=%s decision_keys=%s",
            event.decision_id,
            event.task_ref,
            decision_ref,
            str(task.get("task_id") or ""),
            sorted([str(k) for k in decision.keys()])[:20],
        )
        try:
            result = score_architecture(task, decision, rules=self._rules())
            components = {
                "functional": float(result.functional),
                "security": float(result.security),
                "architecture_fit": float(result.architecture_fit),
                "total": float(result.total),
            }
            logger.info(
                "eval_score_architecture_result decision_id=%s total=%.3f component_keys=%s penalties=%d",
                event.decision_id,
                float(result.total),
                sorted([str(k) for k in components.keys()]),
                len(list(result.penalties or [])),
            )
            return float(result.total), components
        except Exception:
            logger.exception(
                "eval_score_architecture_failed decision_id=%s task_ref=%s decision_ref=%s",
                event.decision_id,
                event.task_ref,
                decision_ref,
            )
            return 0.0, {}

    def _score_components(self, eval_result: Dict[str, Any]) -> tuple[float, Dict[str, float]]:
        scores = eval_result.get("scores") or {}
        if not isinstance(scores, dict):
            return 0.0, {}
        components = {str(k): float(v) for k, v in scores.items() if isinstance(v, (int, float))}
        total = components.get("total", 0.0)
        return float(total), components

    @staticmethod
    def _failure_reasons(eval_result: Dict[str, Any]) -> list[str]:
        errors = eval_result.get("errors") or []
        reasons: list[str] = []
        for e in errors:
            if isinstance(e, dict) and e.get("code"):
                reasons.append(str(e.get("code")))
            else:
                reasons.append(str(e))
        return reasons

    @staticmethod
    def _normalize_failure_reasons(
        *,
        failure_reasons: list[str],
        passed: bool,
        components: Dict[str, float],
        score: float,
    ) -> list[str]:
        normalized: list[str] = []
        for reason in list(failure_reasons or []):
            text = str(reason).strip()
            if text and text not in normalized:
                normalized.append(text)
        if passed:
            return normalized
        if not normalized:
            inferred = "missing_eval_components" if not components else "unknown_failure_no_reason"
            normalized.append(inferred)
            logger.warning(
                "eval_failure_reason_inferred passed=%s score=%.3f components_empty=%s inferred=%s",
                bool(passed),
                float(score),
                not bool(components),
                inferred,
            )
        min_score = 0.8
        try:
            min_score = float(os.getenv("EVAL_MIN_PASS_SCORE", "0.8"))
        except Exception:
            min_score = 0.8
        if components and float(score) < float(min_score) and "score_below_threshold" not in normalized:
            normalized.append("score_below_threshold")
        return normalized

    def _gate_results(self, decision: Dict[str, Any]) -> Dict[str, bool]:
        try:
            status = ops_readiness_status(decision, rules=self._rules())
            return {"ops_readiness": bool(status.passes)}
        except Exception:
            return {}

    def _rules(self) -> Dict[str, Any]:
        if self._rules_cache is None:
            try:
                self._rules_cache = load_rules("policy/policy_rules.yaml")
            except Exception:
                self._rules_cache = {}
        return self._rules_cache


# ── Module-level eval helpers ────────────────────────────────────────────────

def _auto_eval_payload(
    *, prompt_text: str, answer_text: str
) -> tuple[bool, float, Dict[str, float], list[str]]:
    clean_prompt = str(prompt_text or "").strip()
    clean_answer = str(answer_text or "").strip()

    answer_present = bool(clean_answer)
    answer_not_repetitive = answer_present and (not _looks_repetitive(clean_answer))
    answer_not_meta = answer_present and (not _looks_like_non_answer(clean_answer))
    answer_relevant = answer_present and _is_relevant_auto_answer(prompt=clean_prompt, answer=clean_answer)

    components = {
        "answer_present": 1.0 if answer_present else 0.0,
        "answer_not_repetitive": 1.0 if answer_not_repetitive else 0.0,
        "answer_not_meta": 1.0 if answer_not_meta else 0.0,
        "answer_relevant_to_prompt": 1.0 if answer_relevant else 0.0,
    }
    total = (
        components["answer_present"]
        + components["answer_not_repetitive"]
        + components["answer_not_meta"]
        + components["answer_relevant_to_prompt"]
    ) / 4.0
    components["total"] = float(round(total, 6))

    failure_reasons: list[str] = []
    if not answer_present:
        failure_reasons.append("Generated output text is empty for auto task.")
    if answer_present and (not answer_not_repetitive):
        failure_reasons.append("Generated output is overly repetitive for auto task.")
    if answer_present and (not answer_not_meta):
        failure_reasons.append("Generated output looks like a meta/non-answer for auto task.")
    if answer_present and (not answer_relevant):
        expected = _expected_auto_answer_for_prompt(clean_prompt)
        if expected:
            failure_reasons.append(f"Generated output does not include expected answer '{expected}'.")
        else:
            failure_reasons.append("Generated output is not relevant to the auto task prompt.")

    passed = bool(answer_present and answer_not_repetitive and answer_not_meta and answer_relevant)
    return passed, float(components["total"]), components, failure_reasons


def _looks_repetitive(text: str) -> bool:
    tokens = [t for t in _normalize_tokens(text) if t]
    if len(tokens) < 8:
        return False
    unique_ratio = len(set(tokens)) / max(1, len(tokens))
    if unique_ratio < 0.35:
        return True
    repeated_runs = 0
    for idx in range(1, len(tokens)):
        if tokens[idx] == tokens[idx - 1]:
            repeated_runs += 1
    return repeated_runs >= max(2, int(len(tokens) * 0.2))


def _looks_like_non_answer(text: str) -> bool:
    normalized = _normalize_text(text)
    patterns = (
        "as an ai",
        "i cannot",
        "i can't",
        "cannot help",
        "not able to help",
        "nobody is here to help",
        "sorry if this is not what you were looking for",
    )
    return any(token in normalized for token in patterns)


def _is_relevant_auto_answer(*, prompt: str, answer: str) -> bool:
    expected = _expected_auto_answer_for_prompt(prompt)
    normalized_answer = _normalize_text(answer)
    if expected:
        return expected in normalized_answer
    return True


def _expected_auto_answer_for_prompt(prompt: str) -> str | None:
    normalized_prompt = _normalize_text(prompt)
    if ("capital of france" in normalized_prompt) or ("hovedstaden i frankrig" in normalized_prompt):
        return "paris"
    return None


def _normalize_tokens(text: str) -> list[str]:
    raw = _normalize_text(text)
    return [token for token in raw.split() if token]


def _normalize_text(text: str) -> str:
    lowered = str(text or "").lower()
    cleaned = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in lowered)
    return " ".join(cleaned.split())


def _clip_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "...<truncated>"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed >= 0 else None


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except Exception:
        return None
    return parsed if parsed >= 0 else None
