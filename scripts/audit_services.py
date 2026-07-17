#!/usr/bin/env python3
"""
Service Audit Tool

Analyzes all services in the babyAI repository to identify:
- Duplicates and overlaps
- Database integration status
- Service structure consistency
- Consolidation opportunities
"""

import os
import json
from pathlib import Path
from typing import Dict, List, Set
import subprocess

def get_service_info(service_path: Path) -> Dict:
    """Analyze a single service directory"""
    info = {
        "name": service_path.name,
        "has_src": False,
        "has_dockerfile": False,
        "has_requirements": False,
        "has_postgresql": False,
        "has_migrations": False,
        "has_api": False,
        "src_files": [],
        "estimated_lines": 0,
        "dependencies": [],
    }

    # Check for src directory
    src_path = service_path / "src"
    if src_path.exists():
        info["has_src"] = True

        # Count Python files and lines
        for py_file in src_path.rglob("*.py"):
            info["src_files"].append(py_file.name)
            try:
                with open(py_file, 'r', encoding='utf-8', errors='ignore') as f:
                    info["estimated_lines"] += len(f.readlines())
            except:
                pass

        # Check for database integration
        for py_file in src_path.rglob("*.py"):
            if "postgresql" in py_file.name.lower():
                info["has_postgresql"] = True
            if "api" in py_file.name.lower():
                info["has_api"] = True

    # Check for standard files
    info["has_dockerfile"] = (service_path / "Dockerfile").exists()
    info["has_requirements"] = (service_path / "requirements.txt").exists()

    # Check for migrations
    migrations_dirs = ["migrations", "alembic"]
    for migration_dir in migrations_dirs:
        if (service_path / migration_dir).exists():
            info["has_migrations"] = True
            break

    # Extract dependencies from requirements.txt
    req_file = service_path / "requirements.txt"
    if req_file.exists():
        try:
            with open(req_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        # Extract package name (before any version specifier)
                        pkg = line.split(">=")[0].split("==")[0].split("<")[0].split(">")[0]
                        info["dependencies"].append(pkg)
        except:
            pass

    return info

def identify_duplicates(services: List[Dict]) -> List[List[str]]:
    """Identify potential duplicate services based on naming patterns"""
    duplicates = []

    # Group by similar names
    name_groups = {}
    for service in services:
        name = service["name"]

        # Normalize name for grouping
        base_name = name.replace("-", "_").replace("_", "").lower()

        # Group similar services
        if base_name not in name_groups:
            name_groups[base_name] = []
        name_groups[base_name].append(name)

    # Find groups with multiple services
    for base_name, service_names in name_groups.items():
        if len(service_names) > 1:
            duplicates.append(service_names)

    # Check for semantic duplicates
    semantic_groups = [
        ["aesa", "agents", "agent-registry", "repair-agent"],
        ["policy", "policy-validator", "policy_bootstrap"],
        ["tools", "tool-runtime", "skill-runtime"],
        ["claude-video", "voice-runtime", "ui"],
        ["data-exporter", "artifact-writer", "execution-audit", "publisher"],
    ]

    for group in semantic_groups:
        existing_services = [s for s in group if any(svc["name"] == s for svc in services)]
        if len(existing_services) > 1:
            duplicates.append(existing_services)

    return duplicates

def generate_audit_report(services_dir: Path) -> Dict:
    """Generate comprehensive audit report"""

    print("[SCAN] Scanning services directory...")
    services = []

    # Scan all service directories
    for item in services_dir.iterdir():
        if item.is_dir() and not item.name.startswith("_") and not item.name.startswith("."):
            print(f"  Analyzing: {item.name}")
            service_info = get_service_info(item)
            services.append(service_info)

    # Calculate statistics
    total_services = len(services)
    services_with_postgresql = sum(1 for s in services if s["has_postgresql"])
    services_with_migrations = sum(1 for s in services if s["has_migrations"])
    services_with_api = sum(1 for s in services if s["has_api"])
    total_lines = sum(s["estimated_lines"] for s in services)

    # Identify duplicates
    duplicates = identify_duplicates(services)

    # Create report
    report = {
        "summary": {
            "total_services": total_services,
            "services_with_postgresql": services_with_postgresql,
            "services_with_migrations": services_with_migrations,
            "services_with_api": services_with_api,
            "total_estimated_lines": total_lines,
            "potential_duplicates": len(duplicates),
            "postgresql_coverage": f"{(services_with_postgresql/total_services)*100:.1f}%",
        },
        "services": services,
        "duplicates": duplicates,
        "consolidation_candidates": {
            "agent_platform": ["aesa", "agents", "agent-registry", "repair-agent"],
            "policy_management": ["policy", "policy-validator", "policy_bootstrap"],
            "tool_platform": ["tools", "tool-runtime", "skill-runtime"],
            "media_platform": ["claude-video", "voice-runtime", "ui"],
            "data_platform": ["data-exporter", "artifact-writer", "execution-audit", "publisher"],
        }
    }

    return report

def print_audit_summary(report: Dict):
    """Print a human-readable audit summary"""
    summary = report["summary"]

    print("\n" + "="*60)
    print("[AUDIT] BabyAI Services Audit Report")
    print("="*60)

    print(f"\n[OVERVIEW] Summary:")
    print(f"  Total services: {summary['total_services']}")
    print(f"  Services with PostgreSQL: {summary['services_with_postgresql']} ({summary['postgresql_coverage']})")
    print(f"  Services with migrations: {summary['services_with_migrations']}")
    print(f"  Services with API: {summary['services_with_api']}")
    print(f"  Total estimated lines: {summary['total_estimated_lines']:,}")

    print(f"\n[DUPLICATES] Found: {summary['potential_duplicates']} groups")
    for i, dup_group in enumerate(report["duplicates"], 1):
        print(f"  Group {i}: {', '.join(dup_group)}")

    print(f"\n[DATABASE] Integration Status:")
    services_without_db = [s for s in report["services"] if not s["has_postgresql"]]
    print(f"  Services WITHOUT PostgreSQL: {len(services_without_db)}")
    for service in services_without_db[:10]:  # Show first 10
        print(f"    - {service['name']}")
    if len(services_without_db) > 10:
        print(f"    ... and {len(services_without_db) - 10} more")

    print(f"\n[CONSOLIDATION] Opportunities:")
    for platform, services in report["consolidation_candidates"].items():
        existing = [s for s in services if any(svc["name"] == s for svc in report["services"])]
        if len(existing) > 1:
            print(f"  {platform}: {', '.join(existing)} -> 1 service")

    print(f"\n[NEXT] Steps:")
    print("  1. Review duplicate groups for consolidation")
    print("  2. Add PostgreSQL to services without database")
    print("  3. Standardize service structure")
    print("  4. Implement database-per-service pattern")

def main():
    """Main audit function"""
    # Find services directory
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    services_dir = repo_root / "services"

    if not services_dir.exists():
        print(f"[ERROR] Services directory not found: {services_dir}")
        return

    print("[START] Starting BabyAI Services Audit...")

    # Generate audit report
    report = generate_audit_report(services_dir)

    # Save detailed report to JSON
    report_file = repo_root / "docs" / "services-audit-report.json"
    report_file.parent.mkdir(exist_ok=True)

    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2)

    print(f"[SAVED] Detailed report saved to: {report_file}")

    # Print summary
    print_audit_summary(report)

    print(f"\n[DONE] Audit complete! Check {report_file} for full details.")

if __name__ == "__main__":
    main()