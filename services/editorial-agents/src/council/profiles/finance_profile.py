from __future__ import annotations

from babyai.council.profiles.base_profile import BaseProfile


class FinanceProfile(BaseProfile):
    @property
    def domains(self) -> list[str]:
        return ["finance", "risk", "macro"]

    @property
    def agent_roster(self) -> list[str]:
        return [
            "context_agent",
            "evidence_agent",
            "domain_expert",
            "risk_policy_agent",
            "counterargument_agent",
            "historian_agent",
            "planner_agent",
            "evaluator_agent",
        ]

    @property
    def tool_bindings(self) -> dict[str, list[str]]:
        return {
            "context_agent": ["trend_scanner", "news_monitor"],
            "evidence_agent": ["source_validator", "quality_ranker"],
            "domain_expert": ["domain_kb", "quant_reasoner"],
            "risk_policy_agent": ["policy_guard", "compliance_checker"],
            "counterargument_agent": ["stress_tester", "scenario_generator"],
            "historian_agent": ["precedent_index", "base_rate_db"],
            "planner_agent": ["option_generator", "tradeoff_mapper"],
            "evaluator_agent": ["rubric_engine", "score_calibrator"],
        }

    @property
    def risk_thresholds(self) -> dict[str, float]:
        return {
            "max_unverified_evidence_ratio": 0.25,
            "max_constraint_breach_probability": 0.10,
            "max_model_uncertainty": 0.35,
        }

    @property
    def eval_rubric(self) -> dict[str, float]:
        return {
            "evidence_quality": 0.30,
            "risk_adjustment": 0.25,
            "feasibility": 0.20,
            "explainability": 0.15,
            "robustness": 0.10,
        }
