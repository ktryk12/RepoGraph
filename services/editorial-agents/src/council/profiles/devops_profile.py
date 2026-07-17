from __future__ import annotations

from babyai.council.profiles.base_profile import BaseProfile


class DevOpsProfile(BaseProfile):
    @property
    def domains(self) -> list[str]:
        return ["devops", "platform", "reliability"]

    @property
    def agent_roster(self) -> list[str]:
        return [
            "context_agent",
            "evidence_agent",
            "domain_expert",
            "domain_expert",
            "risk_policy_agent",
            "counterargument_agent",
            "planner_agent",
            "evaluator_agent",
        ]

    @property
    def tool_bindings(self) -> dict[str, list[str]]:
        return {
            "context_agent": ["incident_feed", "deploy_timeline"],
            "evidence_agent": ["telemetry_query", "log_sampler"],
            "domain_expert": ["runbook_index", "infra_topology"],
            "risk_policy_agent": ["security_policy", "change_guard"],
            "counterargument_agent": ["chaos_scenarios", "blast_radius_estimator"],
            "historian_agent": ["postmortem_archive", "change_history"],
            "planner_agent": ["rollout_planner", "rollback_planner"],
            "evaluator_agent": ["slo_rubric", "reliability_scorecard"],
        }

    @property
    def risk_thresholds(self) -> dict[str, float]:
        return {
            "max_error_budget_impact": 0.20,
            "max_security_exposure_delta": 0.15,
            "max_rollback_complexity": 0.40,
        }

    @property
    def eval_rubric(self) -> dict[str, float]:
        return {
            "reliability": 0.35,
            "operability": 0.25,
            "security": 0.20,
            "cost_efficiency": 0.10,
            "recovery_speed": 0.10,
        }
