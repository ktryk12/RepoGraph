from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List
import uuid

from agents.base import Agent
from policy.evaluator import EvalValidator
from babyai_shared.bus.interfaces import DecisionEvaluator, HybridGenerator
from babyai_shared.bus.protocol import Context, Message, MessageType
from babyai_shared.contracts.agent_node import AgentNode
from babyai_shared.core.action_proposal import ActionProposal
from babyai_shared.core.hypothesis import Hypothesis
from babyai_shared.core.outcome import Outcome

from ml.hybrid_generator import generate_decision


class ArchitectAgent(AgentNode, Agent):
    def __init__(
        self,
        agent_id: str = "architect-001",
        generator: HybridGenerator | None = None,
        evaluator: DecisionEvaluator | None = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            role="architect",
            accepts={MessageType.ARCHITECTURE_REQUEST},
        )

        self.generator = generator or generate_decision
        self.evaluator = evaluator or EvalValidator()

        self.decisions_made: List[Dict[str, Any]] = []
        self.validation_failures: List[Dict[str, Any]] = []

    def process(self, message: Message, context: Context) -> List[Message]:
        if message.message_type == MessageType.ARCHITECTURE_REQUEST:
            return self._handle_architecture_request(message, context)
        return []

    def _handle_architecture_request(self, message: Message, context: Context) -> List[Message]:
        task_spec = context.task_spec
        if not isinstance(task_spec, dict):
            return [self._create_error_message(message, "No task_spec in context")]

        eval_task = self._convert_to_eval_format(task_spec)

        try:
            decision_dict = self.generator(eval_task)
            context.architecture_decision = decision_dict

            supervised = message.from_agent.startswith("supervisor") or bool(
                message.payload.get("skip_validation")
            )

            if supervised:
                return [Message(
                    message_id=str(uuid.uuid4()),
                    from_agent=self.agent_id,
                    to_agent=message.from_agent,
                    message_type=MessageType.ARCHITECTURE_DECISION,
                    payload={"decision": decision_dict},
                    context_id=context.context_id,
                    timestamp=datetime.now().isoformat(),
                )]

            validation = self.evaluator.validate(decision_dict, eval_task)
            context.validation_results = validation

            if not validation.get("passed", False):
                self.validation_failures.append({
                    "task": eval_task,
                    "decision": decision_dict,
                    "errors": validation.get("errors", []),
                })

                return [Message(
                    message_id=str(uuid.uuid4()),
                    from_agent=self.agent_id,
                    to_agent=message.from_agent,
                    message_type=MessageType.ARCHITECTURE_VALIDATION_FAILED,
                    payload={
                        "errors": validation.get("errors", []),
                        "decision_attempt": decision_dict,
                    },
                    context_id=context.context_id,
                    timestamp=datetime.now().isoformat(),
                )]

            self.decisions_made.append({
                "task": eval_task,
                "decision": decision_dict,
                "validation": validation,
            })

            return [
                Message(
                    message_id=str(uuid.uuid4()),
                    from_agent=self.agent_id,
                    to_agent="translator-001",
                    message_type=MessageType.TRANSLATE_DECISION,
                    payload={"decision_id": decision_dict.get("decision_id")},
                    context_id=context.context_id,
                    timestamp=datetime.now().isoformat(),
                ),
                Message(
                    message_id=str(uuid.uuid4()),
                    from_agent=self.agent_id,
                    to_agent=message.from_agent,
                    message_type=MessageType.ARCHITECTURE_DECISION,
                    payload={"decision_id": decision_dict.get("decision_id")},
                    context_id=context.context_id,
                    timestamp=datetime.now().isoformat(),
                ),
            ]

        except Exception as e:
            return [self._create_error_message(message, str(e))]

    def _convert_to_eval_format(self, task_spec: Dict[str, Any]) -> Dict[str, Any]:
        if "task_id" in task_spec and "spec" in task_spec:
            eval_task = dict(task_spec)
            eval_task["spec"] = self._normalize_spec(eval_task.get("spec", {}))
        else:
            spec = task_spec.get("spec", task_spec)
            eval_task = {
                "task_id": self._make_task_id(),
                "spec": self._normalize_spec(spec if isinstance(spec, dict) else {}),
                "expected": task_spec.get("expected", {
                    "allowed_styles": ["modular_monolith", "hybrid", "microservices"],
                    "must_include": [],
                    "forbidden": [],
                }),
                "scoring": task_spec.get("scoring", {
                    "functional": 0.4,
                    "security": 0.3,
                    "architecture_fit": 0.3,
                }),
            }

        expected = eval_task.get("expected")
        if not isinstance(expected, dict):
            expected = {}
            eval_task["expected"] = expected
        expected.setdefault("allowed_styles", ["modular_monolith", "hybrid", "microservices"])
        expected.setdefault("must_include", [])
        expected.setdefault("forbidden", [])

        scoring = eval_task.get("scoring")
        if not isinstance(scoring, dict):
            scoring = {}
            eval_task["scoring"] = scoring
        scoring.setdefault("functional", 0.4)
        scoring.setdefault("security", 0.3)
        scoring.setdefault("architecture_fit", 0.3)

        return eval_task

    def _normalize_spec(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(spec, dict):
            return {}

        out = dict(spec)

        out.setdefault("id", self._make_spec_id())
        out.setdefault("title", "Untitled")
        out.setdefault("domain", "general")

        user_stories = out.get("user_stories")
        if not isinstance(user_stories, list) or not user_stories:
            user_stories = [{
                "id": "US-001",
                "as_a": "user",
                "i_want": "to complete the primary workflow",
                "so_that": "the product is useful",
                "acceptance_criteria": ["Basic happy-path works"],
            }]
        else:
            for story in user_stories:
                if not isinstance(story, dict):
                    continue
                story.setdefault("id", "US-001")
                story.setdefault("as_a", "user")
                story.setdefault("i_want", "to complete the primary workflow")
                story.setdefault("so_that", "the product is useful")
                ac = story.get("acceptance_criteria")
                if not isinstance(ac, list) or not ac:
                    story["acceptance_criteria"] = ["Basic happy-path works"]
        out["user_stories"] = user_stories

        nfr = out.get("nfr")
        if not isinstance(nfr, dict):
            nfr = {}
            out["nfr"] = nfr

        security = nfr.get("security")
        if not isinstance(security, dict):
            security = {}
            nfr["security"] = security
        security.setdefault("data_classification", "internal")
        auth = security.get("auth")
        if not isinstance(auth, list):
            security["auth"] = ["oidc"]
        security.setdefault("threat_model_required", False)
        security.setdefault("rate_limiting_required", False)

        privacy = nfr.get("privacy")
        if not isinstance(privacy, dict):
            privacy = {}
            nfr["privacy"] = privacy
        privacy.setdefault("gdpr", False)
        privacy.setdefault("data_retention_days", 365)

        reliability = nfr.get("reliability")
        if not isinstance(reliability, dict):
            reliability = {}
            nfr["reliability"] = reliability
        reliability.setdefault("sla", "99.0")
        reliability.setdefault("rpo_minutes", 60)
        reliability.setdefault("rto_minutes", 120)

        performance = nfr.get("performance")
        if not isinstance(performance, dict):
            performance = {}
            nfr["performance"] = performance
        performance.setdefault("p95_latency_ms", 500)
        performance.setdefault("throughput_rps", 50)

        cost = nfr.get("cost")
        if not isinstance(cost, dict):
            cost = {}
            nfr["cost"] = cost
        cost.setdefault("monthly_budget_eur", 500)

        compliance = nfr.get("compliance")
        if not isinstance(compliance, dict):
            compliance = {}
            nfr["compliance"] = compliance
        compliance.setdefault("pci_dss", False)
        compliance.setdefault("sox", False)
        compliance.setdefault("hipaa", False)

        operability = nfr.get("operability")
        if not isinstance(operability, dict):
            operability = {}
            nfr["operability"] = operability
        operability.setdefault("observability_level", "basic")
        operability.setdefault("audit_log_required", False)

        constraints = out.get("constraints")
        if not isinstance(constraints, dict):
            constraints = {}
            out["constraints"] = constraints
        constraints.setdefault("team_size", 3)
        constraints.setdefault("time_to_mvp_days", 60)
        preferred_stack = constraints.get("preferred_stack")
        if not isinstance(preferred_stack, list):
            constraints["preferred_stack"] = []
        deployment = constraints.get("deployment")
        if not isinstance(deployment, str) or deployment not in {"docker", "kubernetes", "vm", "serverless", "baremetal"}:
            constraints["deployment"] = "docker"
        must_support = constraints.get("must_support")
        if not isinstance(must_support, list):
            constraints["must_support"] = []

        return out

    def _make_task_id(self) -> str:
        return f"EVAL-{int(uuid.uuid4().int % 900 + 100):03d}"

    def _make_spec_id(self) -> str:
        now = datetime.now()
        suffix = f"{int(uuid.uuid4().int % 10000):04d}"
        return f"spec_{now.year:04d}_{now.month:02d}_{now.day:02d}_{suffix}"

    def _create_error_message(self, original: Message, error: str) -> Message:
        return Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent=original.from_agent,
            message_type=MessageType.ARCHITECTURE_VALIDATION_FAILED,
            payload={"error": error},
            context_id=original.context_id,
            timestamp=datetime.now().isoformat(),
        )

    def get_failure_patterns(self) -> List[Dict[str, Any]]:
        return self.validation_failures

    def get_success_rate(self) -> float:
        total = len(self.decisions_made) + len(self.validation_failures)
        return 1.0 if total == 0 else (len(self.decisions_made) / total)

    # ------------------------------------------------------------------
    # AgentNode interface (Sprint A6-Adoption)
    # ------------------------------------------------------------------

    def get_state(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "decisions_made": len(self.decisions_made),
            "validation_failures": len(self.validation_failures),
            "success_rate": self.get_success_rate(),
        }

    def form_hypothesis(self, observations: List[Any]) -> Hypothesis:
        return Hypothesis(
            hypothesis_id=str(uuid.uuid4()),
            episode_id="",
            based_on_packets=(),
            summary="Architect: proposed architecture will satisfy all constraints.",
            predicted_effects=("architecture_produced", "constraints_satisfied"),
            reversible=False,
            confidence=self.get_success_rate(),
            timestamp=datetime.utcnow().isoformat(),
        )

    def propose_action(self, hypothesis: Hypothesis) -> ActionProposal:
        return ActionProposal(
            proposal_id=str(uuid.uuid4()),
            episode_id=hypothesis.episode_id,
            proposed_by=self.agent_id,
            reason=hypothesis.summary,
            preconditions=("task_spec_available",),
            expected_effects=hypothesis.predicted_effects,
            risk_score=round(1.0 - hypothesis.confidence, 4),
            timestamp=datetime.utcnow().isoformat(),
        )

    def evaluate_outcome(self, outcome: Outcome) -> float:
        return float(outcome.success_score)
