"""
Test that prevents new cross-service domain imports.

ADR-0015 Rule #3: Services may not import another service's domain logic,
application use cases, stores, repositories, or internal adapters.
"""

import ast
import os
from pathlib import Path
from typing import List, Set
import pytest


def get_service_directories() -> List[Path]:
    """Get all service directories."""
    services_dir = Path(__file__).parent.parent.parent / "services"
    return [d for d in services_dir.iterdir()
            if d.is_dir()
            and d.name != "_template"
            and not d.name.startswith("__")  # Skip __pycache__ etc.
            and not d.name.startswith(".")]  # Skip .git etc.


def extract_imports_from_file(file_path: Path) -> Set[str]:
    """Extract import statements from a Python file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read())
    except (SyntaxError, UnicodeDecodeError):
        return set()

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)

    return imports


def is_forbidden_domain_import(import_name: str, current_service: str) -> bool:
    """Check if an import is a forbidden cross-service domain import."""
    forbidden_patterns = [
        "aesa.application",
        "aesa.domain",
        "aesa.experts",
        "policy.governance",
        "policy.approval",
        "core.orchestrator",
        "core.decision"
    ]

    # Allow imports from own service or shared infrastructure
    allowed_prefixes = [
        "babyai_shared.kafka",
        "babyai_shared.logging",
        "babyai_shared.health",
        "babyai_shared.config",
        "babyai_shared.schemas",
        "babyai_shared.utils",
        "shared.babyai_shared",  # Legacy path
    ]

    # Check if it's a forbidden domain import
    for pattern in forbidden_patterns:
        if import_name.startswith(pattern):
            return True

    # Allow infrastructure imports
    for prefix in allowed_prefixes:
        if import_name.startswith(prefix):
            return False

    return False


def test_no_new_cross_service_domain_imports():
    """
    Test that services don't import cross-service domain logic.

    This maintains a strict allowlist of existing violations during migration.
    New violations are blocked immediately.
    """

    # Explicit allowlist of existing violations (to be removed in later phases)
    # Each entry represents current state that must be migrated in later phases
    MIGRATION_ALLOWLIST = {
        "agent-platform": [
            "aesa.application.use_cases.run_episode",
            "aesa.domain.approval",
            "policy.approval_gate"
        ],
        "context-plane": [
            "aesa.application.ports.expert_serving",
            "aesa.application.use_cases.retrieve_context",
            "aesa.application.use_cases.agent_retrieve_context",
            "aesa.application.ports.context_store"
        ],
        "data-platform": [
            "aesa.application.ports.artifact_writer",
            "aesa.domain.approval",
            "aesa.application.ports.policy_validator"
        ],
        "orchestrator-worker": [
            "policy.governance_smoke",
            "policy.approval_gate"
        ],
        "verify": [
            "policy.approval_gate",
            "policy.governance_smoke"
        ]
    }

    violations = []

    for service_dir in get_service_directories():
        service_name = service_dir.name
        allowed_imports = MIGRATION_ALLOWLIST.get(service_name, [])

        # Scan all Python files in the service
        for python_file in service_dir.rglob("*.py"):
            imports = extract_imports_from_file(python_file)

            for import_name in imports:
                if is_forbidden_domain_import(import_name, service_name):
                    if import_name not in allowed_imports:
                        violations.append({
                            "service": service_name,
                            "file": str(python_file.relative_to(Path.cwd())),
                            "import": import_name,
                            "violation_type": "cross_service_domain_import"
                        })

    if violations:
        violation_details = "\n".join([
            f"  {v['service']}: {v['file']} imports {v['import']}"
            for v in violations
        ])
        pytest.fail(
            f"New cross-service domain imports detected:\n{violation_details}\n\n"
            f"ADR-0015 forbids importing domain logic across service boundaries.\n"
            f"Use Kafka events or explicit HTTP APIs instead.\n"
            f"If this is a migration step, add to MIGRATION_ALLOWLIST with tracking issue."
        )


def test_service_manifest_required_for_new_services():
    """Test that new services must have service_manifest.yaml."""

    # Services that existed before ADR-0015
    LEGACY_SERVICES_WITHOUT_MANIFEST = {
        "aesa", "agent-platform", "billing", "broker-gateway", "claim-detector",
        "config-service", "context-plane", "data-platform", "exercise_runner",
        "expert-serving", "firecrawl-src", "lora-orchestrator", "memory-plane",
        "minimax-coder", "ml", "mmx-media", "orchestrator-worker", "order-manager",
        "planner", "policy-enforcer", "policy-management", "policy-validator",
        "policy_bootstrap", "repair-agent", "request-gate", "skill-runtime",
        "tool-platform", "trust-api", "truthpack-conversation", "verify"
    }

    missing_manifests = []

    for service_dir in get_service_directories():
        service_name = service_dir.name
        manifest_path = service_dir / "service_manifest.yaml"

        if not manifest_path.exists() and service_name not in LEGACY_SERVICES_WITHOUT_MANIFEST:
            missing_manifests.append(service_name)

    if missing_manifests:
        pytest.fail(
            f"New services must have service_manifest.yaml: {missing_manifests}\n"
            f"ADR-0015 requires explicit service metadata for all new services."
        )


if __name__ == "__main__":
    test_no_new_cross_service_domain_imports()
    test_service_manifest_required_for_new_services()
    print("✅ All architecture tests passed")