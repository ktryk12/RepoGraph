#!/usr/bin/env python3
"""
Database Access Pattern Analysis for Phase 2: Database-per-Service

Analyzes current database usage across the codebase to inform service extraction:
1. Maps database file access to services
2. Identifies cross-service database coupling
3. Recommends service ownership for each database
4. Generates migration recommendations

Usage:
    python scripts/phase_2_database_analysis.py [--output analysis_output.json]
"""

import argparse
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Set, Any, Optional
import ast

class DatabaseAccessAnalyzer:
    """Analyzes database access patterns across the codebase."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.analysis_result = {
            "scan_timestamp": None,
            "database_files": {},
            "service_database_access": {},
            "shared_database_violations": {},
            "migration_recommendations": {}
        }

    def analyze(self) -> Dict[str, Any]:
        """Run complete database access analysis."""
        print("Starting database access pattern analysis...")

        # Find all database files
        self._find_database_files()

        # Analyze access patterns by service
        self._analyze_service_access_patterns()

        # Identify shared database violations
        self._identify_sharing_violations()

        # Generate migration recommendations
        self._generate_migration_recommendations()

        return self.analysis_result

    def _find_database_files(self):
        """Find all database files in the repository."""
        print("Finding database files...")

        db_extensions = ['.sqlite', '.sqlite3', '.db']
        database_files = {}

        for ext in db_extensions:
            for db_file in self.repo_root.rglob(f"*{ext}"):
                if self._should_include_database(db_file):
                    relative_path = str(db_file.relative_to(self.repo_root))
                    database_files[relative_path] = {
                        "absolute_path": str(db_file),
                        "size_bytes": db_file.stat().st_size if db_file.exists() else 0,
                        "location_type": self._classify_database_location(relative_path),
                        "accessing_services": [],
                        "import_patterns": []
                    }

        self.analysis_result["database_files"] = database_files
        print(f"Found {len(database_files)} database files")

    def _should_include_database(self, db_path: Path) -> bool:
        """Check if database file should be included in analysis."""
        # Exclude test databases and temporary files
        exclude_patterns = [
            'test',
            'tmp',
            '__pycache__',
            '.git',
            'node_modules'
        ]

        path_str = str(db_path).lower()
        return not any(pattern in path_str for pattern in exclude_patterns)

    def _classify_database_location(self, relative_path: str) -> str:
        """Classify database location type."""
        if relative_path.startswith('shared/'):
            return 'shared'
        elif relative_path.startswith('services/'):
            return 'service_owned'
        elif relative_path.startswith('artifacts/'):
            return 'artifact_storage'
        elif relative_path.startswith('libs/'):
            return 'library'
        else:
            return 'unknown'

    def _analyze_service_access_patterns(self):
        """Analyze how services access databases."""
        print("Analyzing service access patterns...")

        services_dir = self.repo_root / "services"
        if not services_dir.exists():
            return

        service_access = {}

        for service_dir in services_dir.iterdir():
            if not service_dir.is_dir() or service_dir.name.startswith('.'):
                continue

            service_name = service_dir.name
            service_access[service_name] = {
                "database_imports": [],
                "database_paths_referenced": [],
                "sqlite_usage": [],
                "shared_database_access": []
            }

            # Scan Python files in service
            for py_file in service_dir.rglob("*.py"):
                self._analyze_python_file(py_file, service_name, service_access[service_name])

        self.analysis_result["service_database_access"] = service_access

    def _analyze_python_file(self, py_file: Path, service_name: str, service_data: Dict):
        """Analyze a Python file for database access patterns."""
        try:
            with open(py_file, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Look for direct database path references
            db_path_patterns = [
                r'["\']([^"\']*\.sqlite[3]?)["\']',
                r'["\']([^"\']*\.db)["\']',
            ]

            for pattern in db_path_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                for match in matches:
                    if '/' in match or '\\' in match:  # Likely a file path
                        service_data["database_paths_referenced"].append({
                            "path": match,
                            "file": str(py_file.relative_to(self.repo_root)),
                            "context": "direct_path_reference"
                        })

            # Look for sqlite3 module usage
            if 'import sqlite3' in content or 'from sqlite3' in content:
                service_data["sqlite_usage"].append({
                    "file": str(py_file.relative_to(self.repo_root)),
                    "usage_type": "sqlite3_module"
                })

            # Look for shared database imports
            shared_patterns = [
                r'babyai_shared\.knowledge\.registry',
                r'babyai_shared\.provenance',
                r'babyai_shared\.truth',
                r'shared\.babyai_shared'
            ]

            for pattern in shared_patterns:
                if re.search(pattern, content):
                    service_data["shared_database_access"].append({
                        "pattern": pattern,
                        "file": str(py_file.relative_to(self.repo_root)),
                        "violation_type": "shared_database_import"
                    })

            # Look for database-related imports using AST
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        self._analyze_import_node(node, service_name, service_data, py_file)
            except SyntaxError:
                pass  # Skip files with syntax errors

        except (UnicodeDecodeError, PermissionError):
            pass  # Skip files we can't read

    def _analyze_import_node(self, node: ast.AST, service_name: str, service_data: Dict, py_file: Path):
        """Analyze an import node for database-related imports."""
        if isinstance(node, ast.Import):
            for alias in node.names:
                if self._is_database_related_import(alias.name):
                    service_data["database_imports"].append({
                        "import": alias.name,
                        "file": str(py_file.relative_to(self.repo_root)),
                        "import_type": "direct"
                    })
        elif isinstance(node, ast.ImportFrom):
            if node.module and self._is_database_related_import(node.module):
                service_data["database_imports"].append({
                    "import": node.module,
                    "file": str(py_file.relative_to(self.repo_root)),
                    "import_type": "from",
                    "names": [alias.name for alias in node.names]
                })

    def _is_database_related_import(self, import_name: str) -> bool:
        """Check if import is database-related."""
        database_patterns = [
            'sqlite',
            'database',
            'storage',
            'babyai_shared.knowledge',
            'babyai_shared.provenance',
            'babyai_shared.truth',
            'babyai_shared.storage'
        ]

        return any(pattern in import_name.lower() for pattern in database_patterns)

    def _identify_sharing_violations(self):
        """Identify shared database violations."""
        print("Identifying shared database violations...")

        violations = {}

        # Check for services accessing shared databases
        for service_name, access_data in self.analysis_result["service_database_access"].items():
            service_violations = []

            for shared_access in access_data["shared_database_access"]:
                service_violations.append({
                    "violation_type": "shared_database_access",
                    "pattern": shared_access["pattern"],
                    "file": shared_access["file"],
                    "severity": "high"
                })

            for db_path_ref in access_data["database_paths_referenced"]:
                if self._is_shared_database_path(db_path_ref["path"]):
                    service_violations.append({
                        "violation_type": "shared_database_path_reference",
                        "path": db_path_ref["path"],
                        "file": db_path_ref["file"],
                        "severity": "high"
                    })

            if service_violations:
                violations[service_name] = {
                    "violation_count": len(service_violations),
                    "violations": service_violations
                }

        self.analysis_result["shared_database_violations"] = violations

    def _is_shared_database_path(self, path: str) -> bool:
        """Check if database path is in shared location."""
        shared_indicators = [
            'shared/',
            'babyai_shared/',
            '../shared',
            'knowledge/registry',
            'truth/facts',
            'truth/proposals',
            'provenance/provenance'
        ]

        return any(indicator in path for indicator in shared_indicators)

    def _generate_migration_recommendations(self):
        """Generate migration recommendations for each database."""
        print("Generating migration recommendations...")

        recommendations = {}

        # Analyze each database file
        for db_path, db_info in self.analysis_result["database_files"].items():
            recommendation = self._recommend_migration(db_path, db_info)
            recommendations[db_path] = recommendation

        self.analysis_result["migration_recommendations"] = recommendations

    def _recommend_migration(self, db_path: str, db_info: Dict) -> Dict:
        """Recommend migration strategy for a database."""
        recommendation = {
            "current_location": db_info["location_type"],
            "recommended_action": "unknown",
            "target_service": "unknown",
            "migration_complexity": "unknown",
            "priority": "unknown",
            "reasoning": []
        }

        # Shared databases - high priority extraction
        if db_info["location_type"] == "shared":
            recommendation.update({
                "recommended_action": "extract_to_service",
                "migration_complexity": "medium",
                "priority": "high"
            })

            if "knowledge" in db_path:
                recommendation["target_service"] = "knowledge-service"
                recommendation["reasoning"].append("Knowledge registry should be owned by knowledge service")
            elif "truth" in db_path:
                recommendation["target_service"] = "truth-service"
                recommendation["reasoning"].append("Truth data should be consolidated in truth service")
            elif "provenance" in db_path:
                recommendation["target_service"] = "provenance-service"
                recommendation["reasoning"].append("Provenance tracking should have dedicated service")

        # Service-owned databases - verify ownership
        elif db_info["location_type"] == "service_owned":
            service_name = db_path.split('/')[1]  # Extract service name from path
            recommendation.update({
                "recommended_action": "verify_ownership",
                "target_service": service_name,
                "migration_complexity": "low",
                "priority": "medium",
                "reasoning": [f"Database appears to be owned by {service_name}, verify no cross-service access"]
            })

        # Artifact storage - consolidate with appropriate service
        elif db_info["location_type"] == "artifact_storage":
            recommendation.update({
                "recommended_action": "move_to_service",
                "migration_complexity": "low",
                "priority": "medium"
            })

            if "billing" in db_path:
                recommendation["target_service"] = "billing-service"
            elif "context" in db_path:
                recommendation["target_service"] = "context-plane"
            elif "trust" in db_path:
                recommendation["target_service"] = "trust-service"
            else:
                recommendation["target_service"] = "data-platform"

        return recommendation

    def print_summary(self):
        """Print analysis summary."""
        print("\n" + "="*60)
        print("DATABASE ACCESS ANALYSIS SUMMARY")
        print("="*60)

        db_count = len(self.analysis_result["database_files"])
        violation_count = sum(
            data["violation_count"]
            for data in self.analysis_result["shared_database_violations"].values()
        )

        print(f"Database files found: {db_count}")
        print(f"Services with violations: {len(self.analysis_result['shared_database_violations'])}")
        print(f"Total violations: {violation_count}")

        print("\nDatabase locations:")
        location_counts = {}
        for db_info in self.analysis_result["database_files"].values():
            location = db_info["location_type"]
            location_counts[location] = location_counts.get(location, 0) + 1

        for location, count in location_counts.items():
            print(f"  {location}: {count}")

        print("\nTop violations by service:")
        for service, data in sorted(
            self.analysis_result["shared_database_violations"].items(),
            key=lambda x: x[1]["violation_count"],
            reverse=True
        )[:5]:
            print(f"  {service}: {data['violation_count']} violations")

        print("\nHigh-priority migrations:")
        high_priority = [
            db_path for db_path, rec in self.analysis_result["migration_recommendations"].items()
            if rec["priority"] == "high"
        ]
        for db_path in high_priority:
            rec = self.analysis_result["migration_recommendations"][db_path]
            print(f"  {db_path} → {rec['target_service']}")

def main():
    parser = argparse.ArgumentParser(description="Analyze database access patterns for Phase 2")
    parser.add_argument("--output", "-o", help="Output file for analysis results")
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    analyzer = DatabaseAccessAnalyzer(repo_root)

    # Run analysis
    result = analyzer.analyze()

    # Print summary
    analyzer.print_summary()

    # Save results if requested
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"\nDetailed results saved to: {args.output}")

    print("\n" + "="*60)
    print("Analysis complete. Use results to guide Phase 2 implementation.")

if __name__ == "__main__":
    main()