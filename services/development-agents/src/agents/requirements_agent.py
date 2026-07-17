"""
Requirements Agent - converts user input to EVAL task format.

Version 1: Rule-based with keyword detection.
Future: LLM-assisted with validation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List
import uuid

from agents.base import Agent
from babyai_shared.bus.protocol import Context, Message, MessageType


class RequirementsAgent(Agent):
    """
    Converts user input to structured EVAL task specification.

    Accepts: USER_REQUEST
    Emits: REQUIREMENTS_COMPLETE
    """

    def __init__(self, agent_id: str = "requirements-001") -> None:
        super().__init__(
            agent_id=agent_id,
            role="requirements",
            accepts={MessageType.USER_REQUEST},
        )

        self.keyword_rules = self._init_keyword_rules()

    def process(self, message: Message, context: Context) -> List[Message]:
        text = message.payload.get("text", "") or context.user_request or ""
        if not text:
            return []

        eval_task = self._to_eval_task(text)
        context.task_spec = eval_task

        reply_to = message.payload.get("reply_to") or message.from_agent or "orchestrator"

        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent=reply_to,
            message_type=MessageType.REQUIREMENTS_COMPLETE,
            payload={"task_id": eval_task["task_id"]},
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]

    def _to_eval_task(self, text: str) -> Dict[str, Any]:
        text_lower = text.lower()
        task_id = f"EVAL-{int(uuid.uuid4().int % 900 + 100):03d}"

        eval_task = {
            "task_id": task_id,
            "spec": {
                "id": self._make_spec_id(),
                "title": self._extract_title(text),
                "domain": self._detect_domain(text_lower),
                "user_stories": [
                    {
                        "id": "US-001",
                        "as_a": "user",
                        "i_want": "to complete the primary workflow",
                        "so_that": "the product is useful",
                        "acceptance_criteria": [
                            "Given valid input, the main flow succeeds",
                            "Errors are handled and logged",
                        ],
                    }
                ],
                "nfr": self._build_nfr(text_lower),
                "constraints": self._build_constraints(text_lower),
            },
            "expected": {
                "allowed_styles": ["modular_monolith", "hybrid", "microservices"],
                "must_include": self._detect_must_include(text_lower),
                "forbidden": [],
            },
            "scoring": {
                "functional": 0.4,
                "security": 0.3,
                "architecture_fit": 0.3,
            },
        }

        return eval_task

    def _make_spec_id(self) -> str:
        now = datetime.now()
        suffix = f"{int(uuid.uuid4().int % 10000):04d}"
        return f"spec_{now.year:04d}_{now.month:02d}_{now.day:02d}_{suffix}"

    def _extract_title(self, text: str) -> str:
        first_line = text.split("\n")[0].strip()
        return first_line[:50] if first_line else "Auto-generated spec"

    def _detect_domain(self, text: str) -> str:
        if any(kw in text for kw in ["payment", "transaction", "billing"]):
            return "payments"
        if any(kw in text for kw in ["user", "auth", "login", "profile"]):
            return "user_management"
        if any(kw in text for kw in ["ecommerce", "shop", "cart", "order"]):
            return "ecommerce"
        return "general"

    def _build_nfr(self, text: str) -> Dict[str, Any]:
        nfr = {
            "security": {
                "data_classification": "internal",
                "auth": [],
                "threat_model_required": False,
                "rate_limiting_required": False,
            },
            "privacy": {
                "gdpr": False,
                "data_retention_days": 365,
            },
            "reliability": {
                "sla": "99.0",
                "rpo_minutes": 240,
                "rto_minutes": 480,
            },
            "performance": {
                "p95_latency_ms": 800,
                "throughput_rps": 10,
            },
            "cost": {
                "monthly_budget_eur": 200,
            },
            "compliance": {
                "pci_dss": False,
                "sox": False,
                "hipaa": False,
            },
            "operability": {
                "observability_level": "basic",
                "audit_log_required": False,
            },
        }

        if "gdpr" in text or "privacy" in text:
            nfr["privacy"]["gdpr"] = True
            nfr["security"]["data_classification"] = "confidential"

        if "payment" in text or "pci" in text:
            nfr["compliance"]["pci_dss"] = True
            nfr["security"]["threat_model_required"] = True

        if any(kw in text for kw in ["high traffic", "scalability", "scale", "1000+ rps"]):
            nfr["performance"]["throughput_rps"] = 1000
            nfr["reliability"]["sla"] = "99.9"

        if "hipaa" in text or "health" in text:
            nfr["compliance"]["hipaa"] = True
            nfr["security"]["data_classification"] = "confidential"

        if any(kw in text for kw in ["auth", "login", "jwt", "oidc"]):
            nfr["security"]["auth"] = ["oidc"]

        if any(kw in text for kw in ["rate limit", "throttle", "abuse"]):
            nfr["security"]["rate_limiting_required"] = True

        return nfr

    def _build_constraints(self, text: str) -> Dict[str, Any]:
        constraints = {
            "team_size": 2,
            "time_to_mvp_days": 14,
            "preferred_stack": [],
            "deployment": "docker",
            "must_support": [],
        }

        if "python" in text:
            constraints["preferred_stack"].append("python")
        if "java" in text or "spring" in text:
            constraints["preferred_stack"].append("java")
        if "nodejs" in text or "node.js" in text:
            constraints["preferred_stack"].append("nodejs")

        if "kubernetes" in text or "k8s" in text:
            constraints["deployment"] = "kubernetes"
        if "serverless" in text or "lambda" in text:
            constraints["deployment"] = "serverless"

        if "large team" in text:
            constraints["team_size"] = 10
        elif "small team" in text:
            constraints["team_size"] = 2

        return constraints

    def _detect_must_include(self, text: str) -> List[str]:
        must_include: List[str] = []

        if any(kw in text for kw in ["observability", "monitoring", "metrics"]):
            must_include.append("observability")

        if any(kw in text for kw in ["scalability", "scale", "high traffic"]):
            must_include.append("load_test_plan")

        if "audit" in text:
            must_include.append("audit_log")

        return must_include

    def _init_keyword_rules(self) -> Dict[str, Any]:
        return {}
