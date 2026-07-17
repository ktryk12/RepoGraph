"""Budget-aware analyze-code breakdown planning for shared retrieval."""

from __future__ import annotations

from fastapi import HTTPException

from .models import AnalysisPlan, AnalysisStep, SharedRetrievalRequest, VerificationPlan


_BROAD_ANALYZE_TERMS = (
    "analyze the code",
    "analyze code",
    "analyze our program",
    "understand this repo",
    "understand the repo",
    "understand the codebase",
    "repo overview",
    "architecture overview",
    "analyze the repo",
    "analyze our code",
)


def should_break_down_for_analysis(req: SharedRetrievalRequest, task_family: str) -> bool:
    if req.analysis_step_id:
        return True
    lowered = req.query.strip().lower()
    if any(term in lowered for term in _BROAD_ANALYZE_TERMS):
        return True
    if req.output_profile == "review" and any(
        term in lowered for term in ("analyze", "understand", "overview", "architecture", "repo", "program")
    ):
        return True
    return task_family in {"symbol_lookup", "file_to_symbol_map"} and any(
        term in lowered for term in ("analyze", "understand", "overview", "architecture")
    )


def build_analysis_plan(req: SharedRetrievalRequest) -> AnalysisPlan:
    max_context = max(2048, req.target_context)
    overview_context = min(max_context, 4096)
    service_context = min(max_context, 6000 if req.output_profile in {"patch", "review", "medium"} else 4096)
    flow_context = min(max_context, 6000)
    deep_dive_context = min(max_context, 8192 if req.output_profile in {"review", "medium"} else 6000)
    base_profile = "review" if req.output_profile in {"review", "medium"} else "small"
    deep_profile = "review" if req.output_profile == "review" else ("medium" if req.output_profile == "medium" else "patch")
    query = req.query.strip()

    steps = [
        AnalysisStep(
            step_id="step_repo_overview",
            step_kind="repo_overview",
            objective="Build a high-level overview of the repository, main modules, and responsibilities.",
            step_query=f"{query}. Focus first on a repository overview, major modules, and responsibilities.",
            task_hint="file_to_symbol_map",
            working_set_hint="Prefer summaries, top-level files, public modules, and ownership boundaries.",
            suggested_output_profile=base_profile,
            suggested_target_context=overview_context,
            verification_plan=VerificationPlan(lint=False, typecheck=False, static_analysis=False),
            priority=100,
        ),
        AnalysisStep(
            step_id="step_service_overview",
            step_kind="service_subsystem_overview",
            objective="Identify services, subsystems, and their boundaries.",
            step_query=f"{query}. Summarize services, subsystems, and how responsibilities are split.",
            task_hint="config_dependency_reasoning",
            working_set_hint="Prefer service summaries, configs, ownership markers, and subsystem files.",
            suggested_output_profile=base_profile,
            suggested_target_context=service_context,
            verification_plan=VerificationPlan(lint=False, typecheck=False, static_analysis=False),
            priority=90,
            depends_on=["step_repo_overview"],
        ),
        AnalysisStep(
            step_id="step_high_risk_files",
            step_kind="high_risk_files",
            objective="Find high-risk files, risky symbols, and likely failure points.",
            step_query=f"{query}. Identify the highest-risk files, risky symbols, and likely failure points.",
            task_hint="bug_localization",
            working_set_hint="Prefer high-risk symbols, dense call paths, and files with many dependents.",
            suggested_output_profile="small",
            suggested_target_context=service_context,
            verification_plan=VerificationPlan(tests=[], lint=True, typecheck=False, static_analysis=True),
            priority=80,
            depends_on=["step_repo_overview"],
        ),
        AnalysisStep(
            step_id="step_entrypoints",
            step_kind="entrypoints_execution_flow",
            objective="Trace entrypoints and main execution flow through the codebase.",
            step_query=f"{query}. Trace the main entrypoints and execution flow through the codebase.",
            task_hint="call_chain_reasoning",
            working_set_hint="Prefer entrypoints, top-level commands, routers, workers, and their call chains.",
            suggested_output_profile="patch" if req.output_profile == "patch" else base_profile,
            suggested_target_context=flow_context,
            verification_plan=VerificationPlan(lint=False, typecheck=False, static_analysis=False),
            priority=75,
            depends_on=["step_repo_overview"],
        ),
        AnalysisStep(
            step_id="step_hot_paths",
            step_kind="key_symbols_hot_paths",
            objective="Summarize key symbols, hot paths, and heavily connected call chains.",
            step_query=f"{query}. Summarize the key symbols, hot paths, and heavily connected call chains.",
            task_hint="call_chain_reasoning",
            working_set_hint="Prefer symbols with many callers/callees and execution-critical paths.",
            suggested_output_profile="patch" if req.output_profile == "patch" else base_profile,
            suggested_target_context=flow_context,
            verification_plan=VerificationPlan(lint=False, typecheck=False, static_analysis=False),
            priority=70,
            depends_on=["step_service_overview", "step_entrypoints"],
        ),
        AnalysisStep(
            step_id="step_tests",
            step_kind="tests_verification_targets",
            objective="Identify tests, verification targets, and likely regression surfaces.",
            step_query=f"{query}. Identify the most relevant tests, verification targets, and regression surfaces.",
            task_hint="test_impact_lookup",
            working_set_hint="Prefer test files, affected callers, and nearby verification artifacts.",
            suggested_output_profile="small",
            suggested_target_context=overview_context,
            verification_plan=VerificationPlan(lint=True, typecheck=False, static_analysis=False),
            priority=60,
            depends_on=["step_high_risk_files", "step_entrypoints"],
        ),
        AnalysisStep(
            step_id="step_architecture_risks",
            step_kind="architecture_risks",
            objective="Assess architecture risks, coupling, and operational fragility.",
            step_query=f"{query}. Assess architecture risks, coupling, fragile boundaries, and operational hotspots.",
            task_hint="blast_radius_analysis",
            working_set_hint="Prefer high fan-out modules, risky dependencies, configs, and boundary-crossing call paths.",
            suggested_output_profile=base_profile,
            suggested_target_context=service_context,
            verification_plan=VerificationPlan(lint=False, typecheck=False, static_analysis=True),
            priority=50,
            depends_on=["step_high_risk_files", "step_hot_paths", "step_tests"],
        ),
        AnalysisStep(
            step_id="step_follow_up",
            step_kind="follow_up_deep_dive",
            objective="Prepare a follow-up deep dive on the highest-value unresolved area.",
            step_query=f"{query}. Prepare a focused deep dive on the most important unresolved hotspot.",
            task_hint="targeted_refactor",
            working_set_hint="Prefer one unresolved hotspot, the minimal related files, and verification targets.",
            suggested_output_profile=deep_profile,
            suggested_target_context=deep_dive_context,
            verification_plan=VerificationPlan(lint=True, typecheck=False, static_analysis=True),
            priority=40,
            depends_on=["step_architecture_risks"],
        ),
    ]
    return AnalysisPlan(
        query=req.query,
        rationale=(
            "Broad analyze-code requests are decomposed at retrieval level into smaller working-set-driven "
            "steps so each model call stays precise and budget-aware."
        ),
        steps=steps,
    )


def select_analysis_step(plan: AnalysisPlan, step_id: str | None) -> AnalysisStep:
    if not plan.steps:
        raise HTTPException(status_code=400, detail="Analysis plan has no executable steps")
    if step_id is None:
        return plan.steps[0]
    for step in plan.steps:
        if step.step_id == step_id:
            return step
    raise HTTPException(status_code=400, detail=f"Unknown analysis step: {step_id}")


def request_for_analysis_step(req: SharedRetrievalRequest, step: AnalysisStep) -> SharedRetrievalRequest:
    return req.model_copy(
        update={
            "query": step.step_query,
            "task_hint": step.task_hint or req.task_hint,
            "output_profile": step.suggested_output_profile,
            "target_context": min(req.target_context, step.suggested_target_context),
            "include_analysis_plan": False,
        }
    )
