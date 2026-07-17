#!/usr/bin/env python3
"""
Build Current State Map for BabyAI Architecture

Scans the repository to produce an authoritative current state map
showing services, dependencies, state ownership, and ADR-0015 compliance.
"""

import json
import os
import ast
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Set, Any
import argparse


def scan_service_directory(service_path: Path) -> Dict[str, Any]:
    """Scan a service directory and extract metadata."""
    service_name = service_path.name

    # Check for required files
    has_manifest = (service_path / "service_manifest.yaml").exists()
    has_kafka_contract = (service_path / "kafka_contract.yaml").exists()
    has_dockerfile = (service_path / "Dockerfile").exists()
    has_migrations = (service_path / "migrations").exists()

    # Scan for Python files and imports
    python_files = list(service_path.rglob("*.py"))
    total_python_files = len(python_files)

    # Extract imports
    imports = extract_all_imports(python_files)
    aesa_imports = [imp for imp in imports if imp.startswith("aesa.")]
    policy_imports = [imp for imp in imports if imp.startswith("policy.")]
    shared_imports = [imp for imp in imports if "shared" in imp]

    # Check for state files
    state_files = find_state_files(service_path)

    # Extract Kafka topics if contract exists
    kafka_topics = extract_kafka_topics(service_path) if has_kafka_contract else []

    # Extract HTTP endpoints from manifests/code
    http_endpoints = extract_http_endpoints(service_path)

    return {
        "service_name": service_name,
        "path": str(service_path.relative_to(Path.cwd())),
        "has_manifest": has_manifest,
        "has_kafka_contract": has_kafka_contract,
        "has_dockerfile": has_dockerfile,
        "has_migrations": has_migrations,
        "python_files_count": total_python_files,
        "aesa_imports": aesa_imports,
        "policy_imports": policy_imports,
        "shared_imports": shared_imports,
        "state_files": state_files,
        "kafka_topics": kafka_topics,
        "http_endpoints": http_endpoints,
        "adr_0015_violations": {
            "cross_service_domain_imports": len(aesa_imports) + len(policy_imports),
            "missing_manifest": not has_manifest,
            "kafka_without_contract": bool(kafka_topics) and not has_kafka_contract
        }
    }


def extract_all_imports(python_files: List[Path]) -> List[str]:
    """Extract all import statements from Python files."""
    imports = set()

    for file_path in python_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                tree = ast.parse(f.read())

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.add(node.module)

        except (SyntaxError, UnicodeDecodeError, FileNotFoundError):
            continue

    return sorted(list(imports))


def find_state_files(service_path: Path) -> List[str]:
    """Find SQLite databases and other state files."""
    state_files = []

    # Common state file patterns
    patterns = ["*.db", "*.sqlite", "*.sqlite3"]

    for pattern in patterns:
        for file_path in service_path.rglob(pattern):
            state_files.append(str(file_path.relative_to(Path.cwd())))

    # Check for artifact directories
    artifacts_dir = service_path / "artifacts"
    if artifacts_dir.exists():
        state_files.append(str(artifacts_dir.relative_to(Path.cwd())))

    return state_files


def extract_kafka_topics(service_path: Path) -> List[str]:
    """Extract Kafka topics from contract files or code."""
    topics = []

    # Try to read from kafka_contract.yaml
    contract_file = service_path / "kafka_contract.yaml"
    if contract_file.exists():
        try:
            with open(contract_file, 'r') as f:
                content = f.read()
                # Simple pattern matching for topics (could be improved with YAML parsing)
                import re
                topic_patterns = re.findall(r'topic:\s*([a-zA-Z0-9._-]+)', content)
                topics.extend(topic_patterns)
        except Exception:
            pass

    return topics


def extract_http_endpoints(service_path: Path) -> List[str]:
    """Extract HTTP endpoints from service code."""
    endpoints = []

    # Look for FastAPI/Flask patterns in Python files
    for python_file in service_path.rglob("*.py"):
        try:
            with open(python_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Simple pattern matching for common endpoint patterns
            import re
            fastapi_patterns = re.findall(r'@app\.(get|post|put|delete)\(["\']([^"\']+)["\']', content)
            for method, path in fastapi_patterns:
                endpoints.append(f"{method.upper()} {path}")

        except Exception:
            continue

    return endpoints


def classify_service_for_target_architecture(service_data: Dict[str, Any]) -> Dict[str, str]:
    """Classify service according to target bounded contexts."""

    # Mapping based on implementation plan
    BOUNDED_CONTEXT_MAPPING = {
        "truthpack-conversation": "intake-clarification",
        "planner": "intake-clarification",
        "request-gate": "policy-approval",
        "policy-validator": "policy-approval",
        "policy-bootstrap": "policy-approval",
        "orchestrator-worker": "evaluation-orchestrator",
        "context-plane": "context-platform",
        "memory-plane": "context-platform",
        "expert-serving": "inference-gateway",
        "tool-platform": "execution-runtime",
        "tool-runtime": "execution-runtime",
        "skill-runtime": "execution-runtime",
        "artifact-writer": "artifact-audit",
        "data-platform": "artifact-audit"
    }

    service_name = service_data["service_name"]

    # Determine target bounded context
    target_context = BOUNDED_CONTEXT_MAPPING.get(service_name, "unknown")

    # Determine action needed
    if service_data["has_manifest"] and len(service_data["aesa_imports"]) == 0:
        action = "mature"  # Already well-structured
    elif service_name in ["aesa", "agent-platform"]:
        action = "extract"  # Needs decomposition
    elif target_context == "unknown":
        action = "investigate"  # Unclear purpose
    else:
        action = "consolidate"  # Move to target service

    return {
        "target_bounded_context": target_context,
        "recommended_action": action,
        "adr_0015_compliance": "compliant" if service_data["adr_0015_violations"]["cross_service_domain_imports"] == 0 else "violating"
    }


def build_current_state_map() -> Dict[str, Any]:
    """Build complete current state map."""

    # Get repository root
    repo_root = Path(__file__).parent.parent.parent
    services_dir = repo_root / "services"

    # Scan all services
    services = {}
    total_violations = 0

    for service_path in services_dir.iterdir():
        if service_path.is_dir() and service_path.name != "_template":
            service_data = scan_service_directory(service_path)
            classification = classify_service_for_target_architecture(service_data)

            services[service_data["service_name"]] = {
                **service_data,
                **classification
            }

            total_violations += service_data["adr_0015_violations"]["cross_service_domain_imports"]

    # Check infrastructure state
    infrastructure = {
        "postgres": {
            "babyai_postgres_exists": False,  # Target state
            "current_state": "SQLite per service"
        },
        "kafka": {
            "services_with_contracts": len([s for s in services.values() if s["has_kafka_contract"]]),
            "total_kafka_services": len([s for s in services.values() if s["kafka_topics"]])
        },
        "manifests": {
            "services_with_manifests": len([s for s in services.values() if s["has_manifest"]]),
            "total_services": len(services)
        }
    }

    # Identify shared state violations
    shared_violations = identify_shared_state_violations(repo_root)

    return {
        "scan_timestamp": datetime.now().isoformat(),
        "repository_root": str(repo_root),
        "adr_0015_compliance": {
            "total_cross_service_imports": total_violations,
            "services_without_manifests": infrastructure["manifests"]["total_services"] - infrastructure["manifests"]["services_with_manifests"],
            "kafka_services_without_contracts": infrastructure["kafka"]["total_kafka_services"] - infrastructure["kafka"]["services_with_contracts"]
        },
        "services": services,
        "infrastructure": infrastructure,
        "shared_state_violations": shared_violations,
        "next_phases": {
            "phase_0_complete": total_violations == 0,
            "ready_for_kafka_foundation": infrastructure["manifests"]["services_with_manifests"] > 10,
            "ready_for_aesa_decomposition": False  # Requires Phase 1 completion
        }
    }


def identify_shared_state_violations(repo_root: Path) -> Dict[str, Any]:
    """Identify shared state files that violate database-per-service."""
    violations = {
        "shared_sqlite_files": [],
        "multiple_artifact_writers": False,
        "shared_directories": []
    }

    # Find SQLite files in shared locations
    for sqlite_file in repo_root.rglob("*.sqlite*"):
        if "shared/" in str(sqlite_file) or sqlite_file.parent.name == "shared":
            violations["shared_sqlite_files"].append(str(sqlite_file.relative_to(repo_root)))

    # Check artifacts directory for multiple writers
    artifacts_dir = repo_root / "artifacts"
    if artifacts_dir.exists():
        violations["multiple_artifact_writers"] = True  # Needs investigation

    return violations


def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Build BabyAI current state map")
    parser.add_argument("--out", default="artifacts/architecture/current_state_map.json",
                       help="Output file path")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Ensure output directory exists
    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.verbose:
        print("Scanning repository for current state...")

    # Build state map
    state_map = build_current_state_map()

    # Write to file
    with open(output_path, 'w') as f:
        json.dump(state_map, f, indent=2)

    if args.verbose:
        total_services = len(state_map["services"])
        violations = state_map["adr_0015_compliance"]["total_cross_service_imports"]
        manifests = state_map["infrastructure"]["manifests"]["services_with_manifests"]

        print(f"State Map Summary:")
        print(f"   Services found: {total_services}")
        print(f"   Services with manifests: {manifests}")
        print(f"   Cross-service import violations: {violations}")
        print(f"   Output written to: {output_path}")

    return state_map


if __name__ == "__main__":
    main()