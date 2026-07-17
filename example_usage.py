from __future__ import annotations

import importlib
import os

from aesa.application.use_cases.run_episode import RunEpisodeRequest, RunEpisodeUseCase
from aesa.bootstrap.wiring import create_wiring


USER_REQUEST = "I want to build a REST API for users with high scalability"
TASK_SPEC = {
    "spec": {
        "id": "spec_2026_02_06_0001",
        "title": "User Management API",
        "domain": "user_management",
        "user_stories": [
            {
                "id": "US-001",
                "as_a": "client application",
                "i_want": "to create and manage users",
                "so_that": "users can authenticate",
                "acceptance_criteria": [
                    "CRUD operations on users",
                    "Email uniqueness enforced",
                    "Password hashing",
                ],
            }
        ],
        "nfr": {
            "security": {
                "data_classification": "confidential",
                "auth": ["oidc"],
                "threat_model_required": True,
                "rate_limiting_required": True,
            },
            "privacy": {
                "gdpr": True,
                "data_retention_days": 365,
            },
            "reliability": {
                "sla": "99.9",
                "rpo_minutes": 60,
                "rto_minutes": 120,
            },
            "performance": {
                "p95_latency_ms": 200,
                "throughput_rps": 1000,
            },
            "cost": {
                "monthly_budget_eur": 2000,
            },
            "compliance": {
                "pci_dss": False,
                "sox": False,
                "hipaa": False,
            },
            "operability": {
                "observability_level": "standard",
                "audit_log_required": False,
            },
        },
        "constraints": {
            "team_size": 5,
            "time_to_mvp_days": 60,
            "preferred_stack": ["python", "postgres"],
            "deployment": "kubernetes",
            "must_support": ["observability"],
        },
    },
    "expected": {
        "allowed_styles": ["modular_monolith", "hybrid", "microservices"],
        "must_include": ["observability"],
        "forbidden": [],
    },
}

def _load_attr(module_path: str, attr_name: str):
    module = importlib.import_module(module_path)
    return getattr(module, attr_name)


def _build_context_store():
    # REDIS_URL=redis://localhost:6379/0
    # If unset, uses in-memory context store.
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        redis_store_cls = _load_attr("storage.redis_context_store", "RedisContextStore")
        return redis_store_cls(url=redis_url)
    in_memory_store_cls = _load_attr("storage.context_store", "InMemoryContextStore")
    return in_memory_store_cls()


def _bad_generator(task):
    decision = {
        "spec_id": task["spec"]["id"],
        "decision_id": "decision-bad-001",
        "chosen_style": "modular_monolith",
        "topology": {"core": "modular_monolith", "separated_services": []},
        "bounded_contexts": [{"name": "Core", "stability": "stable"}],
        "rationale": [
            {
                "reason": "Small team",
                "signal": "limited delivery capacity",
                "weight": 0.8,
                "evidence_path": "$.spec.constraints.team_size",
            }
        ],
        "risks": [],
        "verification_plan": ["basic smoke"],
        "stop_conditions": [],
    }
    return decision


def _run_aesa_mode() -> None:
    wiring = create_wiring("in_memory")
    use_case = RunEpisodeUseCase()
    request = RunEpisodeRequest(
        task=TASK_SPEC,
        user_request=USER_REQUEST,
        context={"wiring_mode": wiring.mode},
    )
    result = use_case.execute(request)
    print(f"Episode ID: {result.episode_id}")
    print(f"Success: {result.success}")
    print(f"Explanation: {result.explanation}")


def _run_legacy_mode() -> None:
    architect_agent_cls = _load_attr("agents.architect_agent", "ArchitectAgent")
    failure_logger_agent_cls = _load_attr("agents.failure_logger_agent", "FailureLoggerAgent")
    orchestrator_cls = _load_attr("agents.orchestrator", "SimpleOrchestrator")
    registry_cls = _load_attr("agents.registry", "AgentRegistry")
    repair_agent_cls = _load_attr("agents.repair_agent", "RepairAgent")
    requirements_agent_cls = _load_attr("agents.requirements_agent", "RequirementsAgent")
    supervisor_agent_cls = _load_attr("agents.supervisor_agent", "SupervisorAgent")
    translator_agent_cls = _load_attr("agents.translator_agent", "TranslatorAgent")
    validation_agent_cls = _load_attr("agents.validation_agent", "ValidationAgent")
    worker_cls = _load_attr("bus.agent_worker", "AgentWorker")
    in_memory_bus_cls = _load_attr("bus.in_memory", "InMemoryBus")

    registry = registry_cls()
    registry.register(supervisor_agent_cls())
    registry.register(requirements_agent_cls())
    registry.register(architect_agent_cls())
    registry.register(validation_agent_cls())
    registry.register(repair_agent_cls())
    registry.register(translator_agent_cls())
    registry.register(failure_logger_agent_cls())

    context_store = _build_context_store()
    orchestrator = orchestrator_cls(registry, context_store)
    bus = in_memory_bus_cls()
    worker = worker_cls(bus=bus, registry=registry, context_store=context_store)

    explanation = orchestrator.handle_user_request(USER_REQUEST, TASK_SPEC)
    print(explanation)

    print("\n--- Bus layer example (in-memory) ---\n")
    worker.submit_user_request(USER_REQUEST, TASK_SPEC)

    user_messages = []

    def bus_handler(msg):
        if msg.to_agent == "user":
            user_messages.append(msg)
            return
        worker.handle_message(msg)

    while True:
        processed = bus.subscribe(bus_handler, max_messages=50)
        if processed == 0:
            break

    for msg in user_messages:
        print(msg.payload.get("explanation", ""))

    print("\n--- Bus layer fail-and-repair demo ---\n")

    repair_registry = registry_cls()
    repair_registry.register(supervisor_agent_cls())
    repair_registry.register(requirements_agent_cls())
    repair_registry.register(architect_agent_cls(generator=_bad_generator))
    repair_registry.register(validation_agent_cls())
    repair_registry.register(repair_agent_cls())
    repair_registry.register(translator_agent_cls())
    repair_registry.register(failure_logger_agent_cls(log_path="logs/failures-demo.jsonl"))

    repair_bus = in_memory_bus_cls()
    repair_context_store = _build_context_store()
    repair_worker = worker_cls(bus=repair_bus, registry=repair_registry, context_store=repair_context_store)
    repair_user_messages = []

    def repair_bus_handler(msg):
        if msg.to_agent == "user":
            repair_user_messages.append(msg)
            return
        repair_worker.handle_message(msg)

    repair_worker.submit_user_request(
        "Need high traffic API with load testing",
        {
            "task_id": "EVAL-200",
            "spec": TASK_SPEC["spec"],
            "expected": {
                "allowed_styles": ["modular_monolith", "hybrid", "microservices"],
                "must_include": ["load_test_plan"],
                "forbidden": [],
            },
            "scoring": {"functional": 0.4, "security": 0.3, "architecture_fit": 0.3},
        },
    )

    while True:
        processed = repair_bus.subscribe(repair_bus_handler, max_messages=50)
        if processed == 0:
            break

    for msg in repair_user_messages:
        print(msg.payload.get("explanation", ""))


def main() -> None:
    if os.getenv("AESA_MODE") == "1":
        print("AESA_MODE=1 enabled")
        _run_aesa_mode()
        return
    _run_legacy_mode()


if __name__ == "__main__":
    main()
