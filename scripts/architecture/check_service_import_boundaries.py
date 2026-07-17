#!/usr/bin/env python3
"""
Service Import Boundary Checker

Validates that services only import from their allowed packages.
Following ADR-0015 Phase 4 build and import boundary hardening.

Usage:
    python scripts/architecture/check_service_import_boundaries.py
    python scripts/architecture/check_service_import_boundaries.py --service context-plane
    python scripts/architecture/check_service_import_boundaries.py --fix-violations
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
class ImportViolation:
    """Represents an import boundary violation."""

    service_name: str
    file_path: str
    line_number: int
    import_statement: str
    violation_type: str
    allowed_packages: List[str]


@dataclass
class ImportReport:
    """Import boundary validation report."""

    total_files_checked: int
    total_violations: int
    violations_by_service: Dict[str, int]
    violations: List[ImportViolation]
    clean_services: List[str]


class ImportBoundaryChecker:
    """Checks service import boundaries according to ADR-0015 Phase 4 rules."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.services_dir = project_root / "services"

        # Define allowed imports per service according to Phase 4 rules
        self.allowed_imports = self._load_import_allowlists()

        # Global allowed imports for all services
        self.global_allowed = {
            "libs.babyai-schemas",
            "libs.babyai-bus",
            "libs.babyai-observability",
            "libs.babyai-resilience",
            "libs.babyai-config-client",
            "libs.babyai-utils"
        }

        # Forbidden import patterns
        self.forbidden_patterns = {
            "aesa.",  # AESA imports banned (except migration allowlist)
            "services.",  # Cross-service imports banned
            "policy.",  # Policy imports banned (unless inside policy owner)
            "agents.",  # Agent imports banned (outside agent owner)
            "tool_platform.skills."  # Skills imports banned (outside tool-platform)
        }

    def _load_import_allowlists(self) -> Dict[str, Set[str]]:
        """Load import allowlists for each service."""
        allowlists = {}

        # Default allowlist for all services
        default_allowed = {
            "typing",
            "dataclasses",
            "datetime",
            "json",
            "logging",
            "os",
            "sys",
            "pathlib",
            "asyncio",
            "time",
            "uuid",
            "hashlib",
            "enum",
            "abc",
            "collections",
            "functools",
            "itertools",
            "re",
            # FastAPI and web frameworks
            "fastapi",
            "uvicorn",
            "starlette",
            "pydantic",
            # HTTP clients
            "httpx",
            "requests",
            # Database
            "sqlalchemy",
            "sqlite3",
            "psycopg2",
            # Kafka
            "aiokafka",
            "kafka",
            # Testing
            "pytest",
            "unittest",
            "mock"
        }

        # Service-specific allowlists
        service_specific = {
            "context-plane": {
                "domain.context",  # Own domain
                "babyai_shared.ops.killswitch"  # Temporary during migration
            },
            "orchestrator-worker": {
                "domain.approval",  # Own domain
                "bus.event_schemas",
                "bus.kafka_events",
                "bus.kafka_retry",
                "bus.metrics",
                "babyai_shared.core.logging_milestones",
                "babyai_shared.core.orchestrator",
                "babyai_shared.storage.artifact_store",
                "babyai_shared.storage.context_store",
                "babyai_shared.storage.decision_status_store",
                "babyai_shared.storage.idempotency",
                "babyai_shared.storage.outbox_store",
                "babyai_shared.bus.protocol",
                "babyai_shared.truth.loader",
                "babyai_shared.privacy.gateway",
                "babyai.skills.registry",
                "babyai.skills.router",
                "babyai.skills.loader",
                "agents.registry",
                "agents.video_pipeline_bootstrap"
            },
            "expert-serving": {
                "domain.experts"  # Own domain
            },
            "policy-management": {
                "domain.policy"  # Own domain when created
            }
        }

        # Combine default and service-specific
        for service_dir in self.services_dir.iterdir():
            if service_dir.is_dir():
                service_name = service_dir.name
                allowed = default_allowed.copy()
                allowed.update(self.global_allowed)

                if service_name in service_specific:
                    allowed.update(service_specific[service_name])

                # Allow own service package
                allowed.add(service_name)

                allowlists[service_name] = allowed

        return allowlists

    def check_all_services(self) -> ImportReport:
        """Check import boundaries for all services."""
        violations = []
        total_files = 0
        violations_by_service = {}

        for service_dir in self.services_dir.iterdir():
            if service_dir.is_dir() and service_dir.name != "_template":
                service_violations, service_files = self.check_service(service_dir.name)
                violations.extend(service_violations)
                total_files += service_files
                violations_by_service[service_dir.name] = len(service_violations)

        clean_services = [
            service for service, count in violations_by_service.items()
            if count == 0
        ]

        return ImportReport(
            total_files_checked=total_files,
            total_violations=len(violations),
            violations_by_service=violations_by_service,
            violations=violations,
            clean_services=clean_services
        )

    def check_service(self, service_name: str) -> Tuple[List[ImportViolation], int]:
        """Check import boundaries for a specific service."""
        service_dir = self.services_dir / service_name
        if not service_dir.exists():
            return [], 0

        violations = []
        file_count = 0

        # Check all Python files in the service
        for py_file in service_dir.rglob("*.py"):
            if self._should_check_file(py_file):
                file_count += 1
                file_violations = self._check_file_imports(service_name, py_file)
                violations.extend(file_violations)

        return violations, file_count

    def _should_check_file(self, file_path: Path) -> bool:
        """Determine if a file should be checked."""
        # Skip test files, migrations, __pycache__, etc.
        skip_patterns = {
            "__pycache__",
            ".pyc",
            "test_",
            "_test.py",
            "conftest.py",
            "migrations",
            "__init__.py"  # Skip for now, they're usually just imports
        }

        for pattern in skip_patterns:
            if pattern in str(file_path):
                return False

        return True

    def _check_file_imports(self, service_name: str, file_path: Path) -> List[ImportViolation]:
        """Check imports in a single file."""
        violations = []
        allowed = self.allowed_imports.get(service_name, set())

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            tree = ast.parse(content, filename=str(file_path))

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        violation = self._check_import(
                            service_name, file_path, node.lineno,
                            alias.name, allowed
                        )
                        if violation:
                            violations.append(violation)

                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        violation = self._check_import(
                            service_name, file_path, node.lineno,
                            node.module, allowed
                        )
                        if violation:
                            violations.append(violation)

        except Exception as e:
            # Log parsing errors but continue
            print(f"Warning: Could not parse {file_path}: {e}")

        return violations

    def _check_import(
        self,
        service_name: str,
        file_path: Path,
        line_number: int,
        import_name: str,
        allowed: Set[str]
    ) -> Optional[ImportViolation]:
        """Check if an import is allowed."""

        # Check if import is explicitly allowed
        if self._is_import_allowed(import_name, allowed):
            return None

        # Check for forbidden patterns
        violation_type = self._get_violation_type(import_name)
        if violation_type:
            return ImportViolation(
                service_name=service_name,
                file_path=str(file_path.relative_to(self.project_root)),
                line_number=line_number,
                import_statement=f"import {import_name}",
                violation_type=violation_type,
                allowed_packages=sorted(allowed)
            )

        return None

    def _is_import_allowed(self, import_name: str, allowed: Set[str]) -> bool:
        """Check if an import is in the allowed set."""
        # Direct match
        if import_name in allowed:
            return True

        # Check prefixes (e.g., "os.path" allowed if "os" is allowed)
        for allowed_import in allowed:
            if import_name.startswith(f"{allowed_import}."):
                return True

        return False

    def _get_violation_type(self, import_name: str) -> Optional[str]:
        """Determine the type of violation."""
        for pattern in self.forbidden_patterns:
            if import_name.startswith(pattern):
                return f"forbidden_pattern_{pattern.rstrip('.')}"

        # Check if it's an unknown package (not explicitly allowed)
        if not any(import_name.startswith(std) for std in {"builtins", "sys", "os", "typing"}):
            return "unapproved_import"

        return None

    def generate_report(self, report: ImportReport, format: str = "text") -> str:
        """Generate a formatted report."""
        if format == "json":
            return json.dumps(asdict(report), indent=2)

        # Text format
        lines = [
            "=" * 60,
            "SERVICE IMPORT BOUNDARY VALIDATION REPORT",
            "=" * 60,
            f"Total files checked: {report.total_files_checked}",
            f"Total violations: {report.total_violations}",
            ""
        ]

        if report.clean_services:
            lines.extend([
                "✅ CLEAN SERVICES (no violations):",
                ""
            ])
            for service in sorted(report.clean_services):
                lines.append(f"  • {service}")
            lines.append("")

        if report.violations:
            lines.extend([
                "❌ VIOLATIONS BY SERVICE:",
                ""
            ])

            for service, count in sorted(report.violations_by_service.items()):
                if count > 0:
                    lines.append(f"  • {service}: {count} violations")

            lines.extend([
                "",
                "DETAILED VIOLATIONS:",
                ""
            ])

            for violation in report.violations:
                lines.extend([
                    f"Service: {violation.service_name}",
                    f"File: {violation.file_path}:{violation.line_number}",
                    f"Import: {violation.import_statement}",
                    f"Violation: {violation.violation_type}",
                    f"Allowed: {', '.join(violation.allowed_packages[:5])}{'...' if len(violation.allowed_packages) > 5 else ''}",
                    "-" * 40
                ])
        else:
            lines.extend([
                "🎉 ALL SERVICES CLEAN!",
                "No import boundary violations found."
            ])

        return "\n".join(lines)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Check service import boundaries")
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
        "--fix-violations",
        action="store_true",
        help="Attempt to fix violations (not implemented yet)"
    )

    args = parser.parse_args()

    # Find project root
    project_root = Path(__file__).parent.parent.parent

    checker = ImportBoundaryChecker(project_root)

    if args.service:
        violations, file_count = checker.check_service(args.service)
        report = ImportReport(
            total_files_checked=file_count,
            total_violations=len(violations),
            violations_by_service={args.service: len(violations)},
            violations=violations,
            clean_services=[args.service] if not violations else []
        )
    else:
        report = checker.check_all_services()

    output = checker.generate_report(report, args.format)
    print(output)

    # Exit with error code if violations found
    sys.exit(1 if report.total_violations > 0 else 0)


if __name__ == "__main__":
    main()