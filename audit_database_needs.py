#!/usr/bin/env python3
"""
Database Needs Assessment

Analyzes all services to determine which need PostgreSQL persistence
vs which are stateless/computational services.
"""

import os
import sys
from pathlib import Path
from typing import Dict, List, Set

# Services we've already implemented PostgreSQL for
COMPLETED_SERVICES = {
    'agent-platform', 'development-agents', 'editorial-agents',
    'intelligence-agents', 'media-production-agents',
    'orchestration-agents', 'verification-agents',
    'policy-management', 'context-plane', 'data-platform',
    'memory-plane', 'ml', 'orchestrator-worker', 'tool-platform',
    'ui-service', 'video-service', 'voice-service'
}

# Services that likely DON'T need databases (stateless/computational)
STATELESS_SERVICES = {
    '_template', '__pycache__', '__init__.py', 'latent_encoder.py',
    'firecrawl-src', 'minimax-coder', 'test', 'verify',
    'exercise_runner', 'truthpack-conversation'
}

def analyze_service_database_needs():
    """Analyze which services need database persistence"""
    print("DATABASE NEEDS ASSESSMENT")
    print("="*40)

    results = {
        'needs_database': [],
        'already_has_database': [],
        'probably_stateless': [],
        'unclear': []
    }

    services_path = Path('services')
    if not services_path.exists():
        print("Services directory not found!")
        return results

    for service_dir in services_path.iterdir():
        if not service_dir.is_dir():
            continue

        service_name = service_dir.name

        if service_name in COMPLETED_SERVICES:
            results['already_has_database'].append(service_name)
            continue

        if service_name in STATELESS_SERVICES:
            results['probably_stateless'].append(service_name)
            continue

        # Analyze service structure and code to determine database needs
        needs_analysis = analyze_service_structure(service_dir, service_name)

        if needs_analysis['needs_database']:
            results['needs_database'].append((service_name, needs_analysis['reasons']))
        elif needs_analysis['probably_stateless']:
            results['probably_stateless'].append(service_name)
        else:
            results['unclear'].append((service_name, needs_analysis))

    return results

def analyze_service_structure(service_dir: Path, service_name: str) -> Dict:
    """Analyze individual service to determine database needs"""
    analysis = {
        'needs_database': False,
        'probably_stateless': False,
        'reasons': [],
        'indicators': []
    }

    # Database need indicators
    database_indicators = [
        'models.py', 'model.py', 'database.py', 'db.py',
        'store.py', 'repository.py', 'dao.py'
    ]

    persistence_keywords = [
        'CREATE TABLE', 'INSERT INTO', 'UPDATE', 'DELETE FROM',
        'sqlalchemy', 'psycopg', 'asyncpg', 'postgresql',
        'User', 'Order', 'Transaction', 'Account', 'Record',
        'save', 'persist', 'store', 'crud'
    ]

    stateless_indicators = [
        'computation', 'calculate', 'transform', 'convert',
        'encoder', 'decoder', 'processor', 'handler'
    ]

    try:
        # Check for existing database files
        for py_file in service_dir.rglob('*.py'):
            if py_file.name in database_indicators:
                analysis['needs_database'] = True
                analysis['reasons'].append(f"Has database file: {py_file.name}")

        # Check migrations directory
        migrations_dir = service_dir / 'migrations'
        if migrations_dir.exists():
            analysis['needs_database'] = True
            analysis['reasons'].append("Has migrations directory")

        # Check for database-related code
        code_content = ""
        for py_file in service_dir.rglob('*.py'):
            try:
                with open(py_file, 'r', encoding='utf-8') as f:
                    content = f.read().lower()
                    code_content += content
            except:
                continue

        # Count database indicators
        db_indicators_found = sum(1 for keyword in persistence_keywords if keyword.lower() in code_content)
        stateless_indicators_found = sum(1 for keyword in stateless_indicators if keyword.lower() in code_content)

        if db_indicators_found >= 3:
            analysis['needs_database'] = True
            analysis['reasons'].append(f"Found {db_indicators_found} database-related keywords")

        elif stateless_indicators_found >= 2 and db_indicators_found == 0:
            analysis['probably_stateless'] = True
            analysis['reasons'].append(f"Appears computational/stateless")

        # Service name patterns that suggest database needs
        business_service_patterns = [
            'billing', 'order', 'account', 'user', 'auth',
            'policy', 'trust', 'knowledge', 'service', 'gateway'
        ]

        if any(pattern in service_name.lower() for pattern in business_service_patterns):
            analysis['needs_database'] = True
            analysis['reasons'].append(f"Business service pattern in name: {service_name}")

    except Exception as e:
        analysis['indicators'].append(f"Analysis error: {e}")

    return analysis

def prioritize_database_implementation(needs_database: List) -> Dict[str, List]:
    """Prioritize services for database implementation"""

    priorities = {
        'critical_business': [],
        'policy_governance': [],
        'platform_core': [],
        'supporting': []
    }

    for service_info in needs_database:
        service_name = service_info[0] if isinstance(service_info, tuple) else service_info

        # Critical business services
        if any(pattern in service_name for pattern in ['billing', 'order', 'account', 'expert-serving']):
            priorities['critical_business'].append(service_name)

        # Policy and governance
        elif any(pattern in service_name for pattern in ['policy', 'trust', 'request-gate']):
            priorities['policy_governance'].append(service_name)

        # Core platform services
        elif any(pattern in service_name for pattern in ['knowledge', 'broker', 'gateway', 'skill']):
            priorities['platform_core'].append(service_name)

        # Supporting services
        else:
            priorities['supporting'].append(service_name)

    return priorities

def main():
    """Run database needs assessment"""
    print("COMPREHENSIVE DATABASE NEEDS ASSESSMENT")
    print("="*60)

    results = analyze_service_database_needs()

    print(f"\n[+] ALREADY HAVE DATABASE ({len(results['already_has_database'])}):")
    for service in sorted(results['already_has_database'])[:10]:  # Show first 10
        print(f"  - {service}")
    if len(results['already_has_database']) > 10:
        print(f"  ... and {len(results['already_has_database'])-10} more")

    print(f"\n[!] NEED DATABASE ({len(results['needs_database'])}):")
    for item in results['needs_database']:
        if isinstance(item, tuple):
            service, reasons = item
            print(f"  - {service}: {'; '.join(reasons[:2])}")
        else:
            print(f"  - {item}")

    print(f"\n[~] PROBABLY STATELESS ({len(results['probably_stateless'])}):")
    for service in sorted(results['probably_stateless']):
        print(f"  - {service}")

    if results['unclear']:
        print(f"\n[?] UNCLEAR ({len(results['unclear'])}):")
        for item in results['unclear'][:5]:  # Show first 5
            service = item[0] if isinstance(item, tuple) else item
            print(f"  - {service}")

    # Prioritization
    if results['needs_database']:
        print(f"\n[*] IMPLEMENTATION PRIORITY:")
        priorities = prioritize_database_implementation(results['needs_database'])

        for priority, services in priorities.items():
            if services:
                print(f"  {priority.replace('_', ' ').title()}: {len(services)} services")
                for service in services:
                    print(f"    - {service}")

    total_services = len(os.listdir('services'))
    implemented = len(results['already_has_database'])
    needed = len(results['needs_database'])

    print(f"\n[=] SUMMARY:")
    print(f"Total Services: {total_services}")
    print(f"Already Implemented: {implemented} ({implemented/total_services*100:.1f}%)")
    print(f"Need Implementation: {needed} ({needed/total_services*100:.1f}%)")
    print(f"Coverage: {implemented/(implemented+needed)*100:.1f}% of services needing databases")

if __name__ == "__main__":
    main()