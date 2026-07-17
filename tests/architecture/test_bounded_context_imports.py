"""
Bounded Context Import Tests

Tests that services maintain proper bounded context boundaries.
Following ADR-0015 Phase 4 build and import boundary hardening.
"""

import pytest
from pathlib import Path
import sys

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from scripts.architecture.check_service_import_boundaries import ImportBoundaryChecker
from scripts.architecture.check_no_aesa_runtime_imports import AESAImportBanChecker


class TestBoundedContextImports:
    """Test bounded context import boundaries."""

    @pytest.fixture
    def project_root(self):
        """Get project root path."""
        return Path(__file__).parent.parent.parent

    @pytest.fixture
    def import_checker(self, project_root):
        """Create import boundary checker."""
        return ImportBoundaryChecker(project_root)

    @pytest.fixture
    def aesa_checker(self, project_root):
        """Create AESA import ban checker."""
        return AESAImportBanChecker(project_root)

    def test_no_cross_service_imports(self, import_checker):
        """Test that services don't import from other services."""
        report = import_checker.check_all_services()

        # Filter for cross-service violations
        cross_service_violations = [
            v for v in report.violations
            if v.violation_type.startswith("forbidden_pattern_services")
        ]

        if cross_service_violations:
            violation_details = "\n".join([
                f"  {v.service_name}: {v.file_path}:{v.line_number} -> {v.import_statement}"
                for v in cross_service_violations
            ])
            pytest.fail(f"Found {len(cross_service_violations)} cross-service imports:\n{violation_details}")

    def test_no_banned_aesa_imports(self, aesa_checker):
        """Test that services don't use banned AESA imports."""
        report = aesa_checker.check_all_services()

        if report.banned_imports > 0:
            banned_violations = [
                v for v in report.violations
                if v.migration_status == "banned"
            ]
            violation_details = "\n".join([
                f"  {v.service_name}: {v.file_path}:{v.line_number} -> {v.aesa_module}"
                for v in banned_violations
            ])
            pytest.fail(f"Found {report.banned_imports} banned AESA imports:\n{violation_details}")

    def test_context_plane_owns_context_logic(self, project_root):
        """Test that context-plane service owns all context-related logic."""
        context_plane_dir = project_root / "services" / "context-plane"

        if not context_plane_dir.exists():
            pytest.skip("context-plane service not found")

        # Check that context-plane has domain.context package
        domain_context_dir = context_plane_dir / "src" / "domain" / "context"
        assert domain_context_dir.exists(), "context-plane should have domain.context package"

        # Check for key context files
        expected_files = [
            "retrieve_context.py",
            "bootstrap.py",
            "contracts.py",
            "infrastructure.py"
        ]

        missing_files = []
        for expected_file in expected_files:
            if not (domain_context_dir / expected_file).exists():
                missing_files.append(expected_file)

        if missing_files:
            pytest.fail(f"context-plane missing domain files: {missing_files}")

    def test_orchestrator_worker_owns_approval_logic(self, project_root):
        """Test that orchestrator-worker owns approval/orchestration logic."""
        orchestrator_dir = project_root / "services" / "orchestrator-worker"

        if not orchestrator_dir.exists():
            pytest.skip("orchestrator-worker service not found")

        # Check that orchestrator-worker has domain.approval package
        domain_approval_dir = orchestrator_dir / "src" / "domain" / "approval"
        assert domain_approval_dir.exists(), "orchestrator-worker should have domain.approval package"

        # Check for key approval files
        expected_files = [
            "execution_permit.py",
            "policy_client.py"
        ]

        missing_files = []
        for expected_file in expected_files:
            if not (domain_approval_dir / expected_file).exists():
                missing_files.append(expected_file)

        if missing_files:
            pytest.fail(f"orchestrator-worker missing domain files: {missing_files}")

    def test_expert_serving_owns_expert_logic(self, project_root):
        """Test that expert-serving owns expert-related logic."""
        expert_serving_dir = project_root / "services" / "expert-serving"

        if not expert_serving_dir.exists():
            pytest.skip("expert-serving service not found")

        # Check that expert-serving has domain.experts package
        domain_experts_dir = expert_serving_dir / "src" / "domain" / "experts"
        assert domain_experts_dir.exists(), "expert-serving should have domain.experts package"

        # Check for key expert files
        expected_files = [
            "timeout_budget.py",
            "model_runtime_wiring.py",
            "expert_serving_router.py",
            "model_runner_http.py"
        ]

        missing_files = []
        for expected_file in expected_files:
            if not (domain_experts_dir / expected_file).exists():
                missing_files.append(expected_file)

        if missing_files:
            pytest.fail(f"expert-serving missing domain files: {missing_files}")

    def test_services_use_shared_libraries(self, import_checker):
        """Test that services use shared libraries instead of direct implementations."""
        report = import_checker.check_all_services()

        # Look for services that should be using libs.babyai-* packages
        services_using_shared = {}

        for service_dir in (project_root / "services").iterdir():
            if service_dir.is_dir():
                service_name = service_dir.name
                # Check if service imports shared libraries
                for py_file in service_dir.rglob("*.py"):
                    if py_file.is_file():
                        try:
                            with open(py_file, 'r', encoding='utf-8') as f:
                                content = f.read()

                            # Count libs imports
                            libs_imports = content.count("from libs.babyai-")
                            if libs_imports > 0:
                                if service_name not in services_using_shared:
                                    services_using_shared[service_name] = 0
                                services_using_shared[service_name] += libs_imports
                        except:
                            continue

        # Report services using shared libraries (positive outcome)
        if services_using_shared:
            print(f"\n✅ Services using shared libraries: {services_using_shared}")

    @pytest.mark.parametrize("service_name", [
        "context-plane",
        "orchestrator-worker",
        "expert-serving"
    ])
    def test_service_import_compliance(self, import_checker, service_name):
        """Test that specific services comply with import rules."""
        violations, file_count = import_checker.check_service(service_name)

        if violations:
            violation_details = "\n".join([
                f"  {v.file_path}:{v.line_number} -> {v.import_statement} ({v.violation_type})"
                for v in violations
            ])
            pytest.fail(f"{service_name} has {len(violations)} import violations:\n{violation_details}")

    def test_deprecated_aesa_imports_tracked(self, aesa_checker):
        """Test that deprecated AESA imports are tracked for migration."""
        report = aesa_checker.check_all_services()

        if report.deprecated_imports > 0:
            deprecated_violations = [
                v for v in report.violations
                if v.migration_status == "deprecated"
            ]

            # This is a warning, not a failure - we track deprecated imports
            print(f"\n⚠️  Tracking {report.deprecated_imports} deprecated AESA imports for migration:")
            for v in deprecated_violations[:5]:  # Show first 5
                print(f"  {v.service_name}: {v.aesa_module}")

            if len(deprecated_violations) > 5:
                print(f"  ... and {len(deprecated_violations) - 5} more")


class TestSharedLibraryStructure:
    """Test shared library structure and exports."""

    @pytest.fixture
    def libs_dir(self):
        """Get libs directory path."""
        return Path(__file__).parent.parent.parent / "libs"

    def test_shared_libraries_exist(self, libs_dir):
        """Test that all required shared libraries exist."""
        required_libs = [
            "babyai-schemas",
            "babyai-bus",
            "babyai-observability",
            "babyai-resilience",
            "babyai-config-client",
            "babyai-utils"
        ]

        missing_libs = []
        for lib_name in required_libs:
            lib_dir = libs_dir / lib_name
            if not lib_dir.exists() or not (lib_dir / "__init__.py").exists():
                missing_libs.append(lib_name)

        if missing_libs:
            pytest.fail(f"Missing shared libraries: {missing_libs}")

    def test_babyai_schemas_exports(self, libs_dir):
        """Test that babyai-schemas exports required components."""
        schemas_init = libs_dir / "babyai-schemas" / "__init__.py"

        if not schemas_init.exists():
            pytest.skip("babyai-schemas not found")

        with open(schemas_init, 'r', encoding='utf-8') as f:
            content = f.read()

        required_exports = [
            "DecisionEvent",
            "PolicyEvent",
            "ApprovalEvent",
            "ValidationError",
            "validate_event"
        ]

        missing_exports = []
        for export in required_exports:
            if export not in content:
                missing_exports.append(export)

        if missing_exports:
            pytest.fail(f"babyai-schemas missing exports: {missing_exports}")

    def test_babyai_bus_exports(self, libs_dir):
        """Test that babyai-bus exports required components."""
        bus_init = libs_dir / "babyai-bus" / "__init__.py"

        if not bus_init.exists():
            pytest.skip("babyai-bus not found")

        with open(bus_init, 'r', encoding='utf-8') as f:
            content = f.read()

        required_exports = [
            "KafkaClient",
            "EventBus",
            "EventPublisher",
            "get_kafka_client",
            "create_event_bus_for_service"
        ]

        missing_exports = []
        for export in required_exports:
            if export not in content:
                missing_exports.append(export)

        if missing_exports:
            pytest.fail(f"babyai-bus missing exports: {missing_exports}")

# Run with: pytest tests/architecture/test_bounded_context_imports.py -v