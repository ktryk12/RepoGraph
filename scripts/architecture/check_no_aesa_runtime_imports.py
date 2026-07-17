#!/usr/bin/env python3
"""
AESA Runtime Import Ban Checker

Enforces ban on AESA runtime imports outside of migration allowlist.
Following ADR-0015 Phase 4 build and import boundary hardening.

Usage:
    python scripts/architecture/check_no_aesa_runtime_imports.py
    python scripts/architecture/check_no_aesa_runtime_imports.py --service context-plane
    python scripts/architecture/check_no_aesa_runtime_imports.py --update-allowlist
"""

import ast
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from dataclasses import dataclass, asdict


@dataclass
class AESAImportViolation:
    """Represents an AESA import violation."""

    service_name: str
    file_path: str
    line_number: int
    import_statement: str
    aesa_module: str
    migration_status: str  # "allowed", "deprecated", "banned"


@dataclass
class AESAImportReport:
    """AESA import validation report."""

    total_files_checked: int
    total_violations: int
    allowed_migrations: int
    deprecated_imports: int
    banned_imports: int
    violations_by_service: Dict[str, int]
    violations: List[AESAImportViolation]


class AESAImportBanChecker:
    """Checks for banned AESA imports according to Phase 4 rules."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.services_dir = project_root / "services"

        # Migration allowlist - AESA imports temporarily allowed during transition
        self.migration_allowlist = {
            "context-plane": {
                # These should eventually be removed in favor of domain.context
                "aesa.application.use_cases.retrieve_context": "deprecated",
                "aesa.application.use_cases.agent_retrieve_context": "deprecated",
                "aesa.infrastructure.sqlite_context_store": "deprecated",
                "aesa.bootstrap.wiring": "deprecated"
            },
            "orchestrator-worker": {
                # These should eventually be removed in favor of domain.approval
                "aesa.domain.approval": "deprecated",
                "aesa.bootstrap.orchestrator_wiring": "deprecated"
            },
            "expert-serving": {
                # These should eventually be removed in favor of domain.experts
                "aesa.core.timeout_budget": "deprecated",
                "aesa.bootstrap.model_runtime_wiring": "deprecated",
                "aesa.infrastructure.expert_serving_router": "deprecated",
                "aesa.infrastructure.model_runner_http": "deprecated"
            },
            "data-platform": {
                # These may be allowed temporarily
                "aesa.domain.approval": "deprecated",
                "aesa.utils": "deprecated"
            }
        }

        # Completely banned AESA modules (no exceptions)
        self.banned_aesa_modules = {
            "aesa.domain.orchestrator",
            "aesa.domain.experts.selection",
            "aesa.domain.truth",
            "aesa.application.ports.orchestrator",
            "aesa.application.ports.experts",
            "aesa.infrastructure.truth_store",
            "aesa.scoring",
            "aesa.api"
        }

    def check_all_services(self) -> AESAImportReport:
        """Check AESA imports for all services."""
        violations = []
        total_files = 0
        violations_by_service = {}
        allowed_migrations = 0
        deprecated_imports = 0
        banned_imports = 0

        for service_dir in self.services_dir.iterdir():
            if service_dir.is_dir() and service_dir.name != "_template":
                service_violations, service_files, service_stats = self.check_service(service_dir.name)
                violations.extend(service_violations)
                total_files += service_files
                violations_by_service[service_dir.name] = len(service_violations)

                allowed_migrations += service_stats["allowed"]
                deprecated_imports += service_stats["deprecated"]
                banned_imports += service_stats["banned"]

        return AESAImportReport(
            total_files_checked=total_files,
            total_violations=len(violations),
            allowed_migrations=allowed_migrations,
            deprecated_imports=deprecated_imports,
            banned_imports=banned_imports,
            violations_by_service=violations_by_service,
            violations=violations
        )

    def check_service(self, service_name: str) -> Tuple[List[AESAImportViolation], int, Dict[str, int]]:
        """Check AESA imports for a specific service."""
        service_dir = self.services_dir / service_name
        if not service_dir.exists():
            return [], 0, {"allowed": 0, "deprecated": 0, "banned": 0}

        violations = []
        file_count = 0
        stats = {"allowed": 0, "deprecated": 0, "banned": 0}

        # Check all Python files in the service
        for py_file in service_dir.rglob("*.py"):
            if self._should_check_file(py_file):
                file_count += 1
                file_violations, file_stats = self._check_file_aesa_imports(service_name, py_file)
                violations.extend(file_violations)

                for key in stats:
                    stats[key] += file_stats[key]

        return violations, file_count, stats

    def _should_check_file(self, file_path: Path) -> bool:
        """Determine if a file should be checked."""
        # Skip test files, migrations, __pycache__, etc.
        skip_patterns = {
            "__pycache__",
            ".pyc",
            "test_",
            "_test.py",
            "conftest.py",
            "migrations"
        }

        for pattern in skip_patterns:
            if pattern in str(file_path):
                return False

        return True

    def _check_file_aesa_imports(self, service_name: str, file_path: Path) -> Tuple[List[AESAImportViolation], Dict[str, int]]:
        """Check AESA imports in a single file."""
        violations = []
        stats = {"allowed": 0, "deprecated": 0, "banned": 0}

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            tree = ast.parse(content, filename=str(file_path))

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("aesa."):
                            violation, status = self._check_aesa_import(
                                service_name, file_path, node.lineno, alias.name
                            )
                            if violation:
                                violations.append(violation)
                            stats[status] += 1

                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("aesa."):
                        violation, status = self._check_aesa_import(
                            service_name, file_path, node.lineno, node.module
                        )
                        if violation:
                            violations.append(violation)
                        stats[status] += 1

        except Exception as e:
            # Log parsing errors but continue
            print(f"Warning: Could not parse {file_path}: {e}")

        return violations, stats

    def _check_aesa_import(
        self,
        service_name: str,
        file_path: Path,
        line_number: int,
        aesa_module: str
    ) -> Tuple[Optional[AESAImportViolation], str]:
        """Check if an AESA import is allowed."""

        # Check if completely banned
        if aesa_module in self.banned_aesa_modules:
            return AESAImportViolation(
                service_name=service_name,
                file_path=str(file_path.relative_to(self.project_root)),
                line_number=line_number,
                import_statement=f"import {aesa_module}",
                aesa_module=aesa_module,
                migration_status="banned"
            ), "banned"

        # Check migration allowlist
        service_allowlist = self.migration_allowlist.get(service_name, {})
        if aesa_module in service_allowlist:
            status = service_allowlist[aesa_module]
            if status == "deprecated":
                return AESAImportViolation(
                    service_name=service_name,
                    file_path=str(file_path.relative_to(self.project_root)),
                    line_number=line_number,
                    import_statement=f"import {aesa_module}",
                    aesa_module=aesa_module,
                    migration_status="deprecated"
                ), "deprecated"
            else:
                return None, "allowed"

        # Default: ban all other AESA imports
        return AESAImportViolation(
            service_name=service_name,
            file_path=str(file_path.relative_to(self.project_root)),
            line_number=line_number,
            import_statement=f"import {aesa_module}",
            aesa_module=aesa_module,
            migration_status="banned"
        ), "banned"

    def generate_report(self, report: AESAImportReport, format: str = "text") -> str:
        """Generate a formatted report."""
        if format == "json":
            return json.dumps(asdict(report), indent=2)

        # Text format
        lines = [
            "=" * 60,
            "AESA IMPORT BAN ENFORCEMENT REPORT",
            "=" * 60,
            f"Total files checked: {report.total_files_checked}",
            f"Total AESA imports found: {report.allowed_migrations + report.deprecated_imports + report.banned_imports}",
            f"  • Allowed migrations: {report.allowed_migrations}",
            f"  • Deprecated imports: {report.deprecated_imports}",
            f"  • Banned imports: {report.banned_imports}",
            f"Total violations: {report.total_violations}",
            ""
        ]

        if report.banned_imports == 0 and report.deprecated_imports == 0:
            lines.extend([
                "🎉 NO AESA IMPORT VIOLATIONS!",
                "All services comply with import ban rules."
            ])
        else:
            lines.extend([
                "VIOLATIONS BY SERVICE:",
                ""
            ])

            for service, count in sorted(report.violations_by_service.items()):
                if count > 0:
                    lines.append(f"  • {service}: {count} violations")

            if report.violations:
                lines.extend([
                    "",
                    "DETAILED VIOLATIONS:",
                    ""
                ])

                # Group by violation type
                banned = [v for v in report.violations if v.migration_status == "banned"]
                deprecated = [v for v in report.violations if v.migration_status == "deprecated"]

                if banned:
                    lines.extend([
                        "🚫 BANNED IMPORTS (must be removed):",
                        ""
                    ])
                    for violation in banned:
                        lines.extend([
                            f"  Service: {violation.service_name}",
                            f"  File: {violation.file_path}:{violation.line_number}",
                            f"  Import: {violation.aesa_module}",
                            f"  Status: BANNED - must be replaced with domain logic",
                            ""
                        ])

                if deprecated:
                    lines.extend([
                        "⚠️  DEPRECATED IMPORTS (should be migrated):",
                        ""
                    ])
                    for violation in deprecated:
                        lines.extend([
                            f"  Service: {violation.service_name}",
                            f"  File: {violation.file_path}:{violation.line_number}",
                            f"  Import: {violation.aesa_module}",
                            f"  Status: DEPRECATED - should migrate to domain logic",
                            ""
                        ])

        lines.extend([
            "",
            "MIGRATION GUIDANCE:",
            "• Replace AESA imports with domain logic in services/{service}/src/domain/",
            "• Use libs/babyai-* packages for shared infrastructure",
            "• Convert direct imports to Kafka/HTTP for cross-service communication",
            "• See ADR-0015 Phase 4 for detailed migration steps"
        ])

        return "\n".join(lines)

    def update_migration_allowlist(self, service_name: str, aesa_module: str, status: str) -> None:
        """Update the migration allowlist (for admin use)."""
        if service_name not in self.migration_allowlist:
            self.migration_allowlist[service_name] = {}

        self.migration_allowlist[service_name][aesa_module] = status
        print(f"Updated allowlist: {service_name}.{aesa_module} -> {status}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Check for banned AESA imports")
    parser.add_argument(
        "--service",
        help="Check specific service only"
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format"
    )
    parser.add_argument(
        "--update-allowlist",
        action="store_true",
        help="Interactive mode to update migration allowlist"
    )

    args = parser.parse_args()

    # Find project root
    project_root = Path(__file__).parent.parent.parent

    checker = AESAImportBanChecker(project_root)

    if args.service:
        violations, file_count, stats = checker.check_service(args.service)
        report = AESAImportReport(
            total_files_checked=file_count,
            total_violations=len(violations),
            allowed_migrations=stats["allowed"],
            deprecated_imports=stats["deprecated"],
            banned_imports=stats["banned"],
            violations_by_service={args.service: len(violations)},
            violations=violations
        )
    else:
        report = checker.check_all_services()

    output = checker.generate_report(report, args.format)
    print(output)

    # Exit with error code if banned imports found
    # (deprecated imports are warnings, not failures)
    exit_code = 1 if report.banned_imports > 0 else 0
    sys.exit(exit_code)


if __name__ == "__main__":
    main()