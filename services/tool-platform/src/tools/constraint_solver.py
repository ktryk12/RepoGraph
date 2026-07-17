from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class ExpertPlanSpec:
    expert_id: str
    tier: str
    task_types: List[str]
    capabilities: List[str]
    tags: List[str] = field(default_factory=list)
    gpu_required: bool = False
    memory_mb: int = 512
    max_concurrency: int = 1


@dataclass(frozen=True)
class ExecutionPlan:
    hw_profile: Dict[str, Any]
    plan_name: str
    max_parallel_experts: int
    swarm_size: int
    enable_gpu_experts: bool
    expert_specs: List[ExpertPlanSpec]
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def solve_execution_plan(hw_profile: Dict[str, Any]) -> ExecutionPlan:
    ram_gb = _num(hw_profile.get("ram_gb"), default=16)
    gpu_count = _num(hw_profile.get("gpu_count"), default=0)
    gpu_model = str(hw_profile.get("gpu_model", "")).strip().lower()
    cpu_cores = _num(hw_profile.get("cpu_cores"), default=4)

    specs = _core_specs()
    plan_name = "cpu_minimal"
    max_parallel = 2
    notes = ["CPU-only default plan"]

    if gpu_count > 0:
        plan_name = "gpu_standard"
        max_parallel = 4 if ram_gb < 256 else 5
        specs.extend(_gpu_standard_specs())
        notes = ["GPU detected; enabling gpu tier experts"]

    if gpu_count > 0 and "m6000" in gpu_model and ram_gb >= 512:
        plan_name = "m6000_512gb"
        max_parallel = 8 if cpu_cores >= 16 else 6
        specs = _core_specs() + _gpu_large_specs()
        notes = ["Large-memory M6000 profile; enabling expanded swarm tiers"]

    ordered = sorted(specs, key=lambda s: (_tier_rank(s.tier), s.expert_id))
    swarm_size = len(ordered)

    return ExecutionPlan(
        hw_profile=dict(hw_profile),
        plan_name=plan_name,
        max_parallel_experts=min(max_parallel, swarm_size),
        swarm_size=swarm_size,
        enable_gpu_experts=any(s.gpu_required for s in ordered),
        expert_specs=ordered,
        notes=notes,
    )


def _core_specs() -> List[ExpertPlanSpec]:
    return [
        ExpertPlanSpec(
            expert_id="planner_core",
            tier="core",
            task_types=["analysis", "planning", "repair"],
            capabilities=["rollout_plan", "architecture_patch"],
            tags=["core", "planner"],
            gpu_required=False,
            memory_mb=768,
            max_concurrency=2,
        ),
        ExpertPlanSpec(
            expert_id="risk_scanner",
            tier="core",
            task_types=["analysis", "planning"],
            capabilities=["risk_scan"],
            tags=["core", "risk"],
            gpu_required=False,
            memory_mb=512,
            max_concurrency=2,
        ),
        ExpertPlanSpec(
            expert_id="schema_validation",
            tier="core",
            task_types=["analysis", "validation", "repair"],
            capabilities=["schema_validation"],
            tags=["core", "validator"],
            gpu_required=False,
            memory_mb=512,
            max_concurrency=2,
        ),
        ExpertPlanSpec(
            expert_id="repair_hint_ssrn",
            tier="core",
            task_types=["analysis", "repair", "coding"],
            capabilities=["repair_hint", "ssrn_predict", "ssrn_learn"],
            tags=["core", "ssrn", "repair_hint"],
            gpu_required=False,
            memory_mb=256,
            max_concurrency=2,
        ),
    ]


def _gpu_standard_specs() -> List[ExpertPlanSpec]:
    return [
        ExpertPlanSpec(
            expert_id="retrieval_gpu",
            tier="gpu_accel",
            task_types=["analysis", "planning"],
            capabilities=["context_retrieval"],
            tags=["gpu", "retrieval"],
            gpu_required=True,
            memory_mb=2048,
            max_concurrency=1,
        ),
    ]


def _gpu_large_specs() -> List[ExpertPlanSpec]:
    return [
        ExpertPlanSpec(
            expert_id="policy_guard",
            tier="extended",
            task_types=["analysis", "repair"],
            capabilities=["policy_filter", "conflict_review"],
            tags=["extended", "policy"],
            gpu_required=False,
            memory_mb=1024,
            max_concurrency=2,
        ),
        ExpertPlanSpec(
            expert_id="retrieval_gpu",
            tier="gpu_accel",
            task_types=["analysis", "planning"],
            capabilities=["context_retrieval"],
            tags=["gpu", "retrieval"],
            gpu_required=True,
            memory_mb=2048,
            max_concurrency=2,
        ),
        ExpertPlanSpec(
            expert_id="code_synth_gpu",
            tier="gpu_accel",
            task_types=["analysis", "repair"],
            capabilities=["code_patch_synthesis"],
            tags=["gpu", "synthesis"],
            gpu_required=True,
            memory_mb=3072,
            max_concurrency=1,
        ),
        ExpertPlanSpec(
            expert_id="simulation_gpu",
            tier="gpu_accel",
            task_types=["analysis"],
            capabilities=["what_if_simulation"],
            tags=["gpu", "simulation"],
            gpu_required=True,
            memory_mb=3072,
            max_concurrency=1,
        ),
    ]


def _num(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _tier_rank(tier: str) -> int:
    order = {"core": 0, "extended": 1, "gpu_accel": 2}
    return order.get(str(tier), 99)
