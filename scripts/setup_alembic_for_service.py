#!/usr/bin/env python3
"""
Systematic Alembic Setup Script

Sets up Alembic configuration for a babyAI service following standardized patterns.

Usage:
    python scripts/setup_alembic_for_service.py <service_name>

Example:
    python scripts/setup_alembic_for_service.py data-platform
    python scripts/setup_alembic_for_service.py media-platform
"""

import os
import sys
import shutil
from pathlib import Path


def setup_alembic_for_service(service_name: str):
    """Set up Alembic configuration for a service"""

    # Validate service directory exists
    service_dir = Path(f"services/{service_name}")
    if not service_dir.exists():
        print(f"ERROR: Service directory 'services/{service_name}' does not exist")
        return False

    # Create migrations directory structure
    migrations_dir = service_dir / "migrations"
    migrations_dir.mkdir(exist_ok=True)

    versions_dir = migrations_dir / "versions"
    versions_dir.mkdir(exist_ok=True)

    # Copy and customize alembic.ini template
    template_dir = Path("templates/alembic")
    alembic_ini_template = template_dir / "alembic.ini"
    alembic_ini_dest = migrations_dir / "alembic.ini"

    if not alembic_ini_template.exists():
        print(f"ERROR: Template file '{alembic_ini_template}' not found")
        return False

    # Read template and substitute service name
    with open(alembic_ini_template, 'r') as f:
        content = f.read()

    # Replace placeholders with actual service name
    content = content.replace("{SERVICE_NAME}", service_name)
    content = content.replace("{service_name}", service_name.replace("-", "_"))

    with open(alembic_ini_dest, 'w') as f:
        f.write(content)

    # Copy env.py template
    env_py_template = template_dir / "env.py"
    env_py_dest = migrations_dir / "env.py"

    if not env_py_template.exists():
        print(f"ERROR: Template file '{env_py_template}' not found")
        return False

    shutil.copy2(env_py_template, env_py_dest)

    # Create __init__.py files for Python package structure
    (migrations_dir / "__init__.py").touch()
    (versions_dir / "__init__.py").touch()

    # Create requirements update suggestion
    requirements_file = service_dir / "requirements.txt"
    alembic_requirement = "alembic>=1.12.0"
    postgres_requirement = "asyncpg>=0.28.0"

    requirements_needs_update = []
    if requirements_file.exists():
        with open(requirements_file, 'r') as f:
            content = f.read()
        if "alembic" not in content:
            requirements_needs_update.append(alembic_requirement)
        if "asyncpg" not in content and "psycopg2" not in content:
            requirements_needs_update.append(postgres_requirement)
    else:
        requirements_needs_update = [alembic_requirement, postgres_requirement]

    # Print setup summary
    print(f"SUCCESS: Alembic setup completed for service '{service_name}'")
    print(f"   DIR: Created: {migrations_dir}")
    print(f"   FILE: Created: {alembic_ini_dest}")
    print(f"   FILE: Created: {env_py_dest}")
    print(f"   DIR: Created: {versions_dir}")

    if requirements_needs_update:
        print(f"\nTODO: Manual step required:")
        print(f"   Add to {requirements_file}:")
        for req in requirements_needs_update:
            print(f"     {req}")

    # Print next steps
    database_name = f"babyai_{service_name.replace('-', '_')}"
    user_name = f"babyai_{service_name.replace('-', '_')}_user"

    print(f"\nTODO: Next steps:")
    print(f"   1. Create database: {database_name}")
    print(f"   2. Create user: {user_name}")
    print(f"   3. Create initial migration:")
    print(f"      cd services/{service_name}")
    print(f"      alembic revision --autogenerate -m \"Initial {service_name} schema\"")
    print(f"   4. Run migration:")
    print(f"      alembic upgrade head")
    print(f"   5. Add to docker-compose.yml if service needs deployment")

    return True


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/setup_alembic_for_service.py <service_name>")
        print("Example: python scripts/setup_alembic_for_service.py data-platform")
        sys.exit(1)

    service_name = sys.argv[1]

    # Change to repo root
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    os.chdir(repo_root)

    success = setup_alembic_for_service(service_name)

    if not success:
        sys.exit(1)

    print(f"\nDONE: Alembic setup for '{service_name}' is ready!")


if __name__ == "__main__":
    main()