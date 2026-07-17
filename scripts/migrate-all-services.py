#!/usr/bin/env python3
"""
Master Migration Script for All Services

This script runs database migrations for all microservices that follow the database-per-service pattern.
Each service gets its own dedicated database with isolated schema and connections.

Usage:
  python scripts/migrate-all-services.py [--service video|voice|ui] [--dry-run]

Examples:
  python scripts/migrate-all-services.py                    # Migrate all services
  python scripts/migrate-all-services.py --service voice   # Migrate only voice-service
  python scripts/migrate-all-services.py --dry-run         # Show what would be migrated
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Any


# Service configuration
SERVICES = {
    "video": {
        "name": "video-service",
        "path": "services/video-service",
        "database": "video_service_db",
        "description": "Video generation and rendering service"
    },
    "voice": {
        "name": "voice-service",
        "path": "services/voice-service",
        "database": "voice_service_db",
        "description": "Voice processing service (STT/TTS, MCP integration)"
    },
    "ui": {
        "name": "ui-service",
        "path": "services/ui-service",
        "database": "ui_service_db",
        "description": "User interface and dashboard service"
    }
}


def setup_logging() -> logging.Logger:
    """Setup structured logging"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    return logging.getLogger("migration-master")


def get_repo_root() -> Path:
    """Find repository root directory"""
    script_path = Path(__file__).resolve()
    # Assume script is in scripts/ directory at repo root
    return script_path.parent.parent


def run_service_migration(service_key: str, service_config: Dict[str, Any], repo_root: Path, dry_run: bool = False) -> bool:
    """Run migration for a single service"""
    logger = logging.getLogger(f"migrate-{service_key}")

    service_path = repo_root / service_config["path"]
    migrate_script = service_path / "migrate.py"

    if not migrate_script.exists():
        logger.error(f"Migration script not found: {migrate_script}")
        return False

    logger.info(f"{'[DRY-RUN] ' if dry_run else ''}Migrating {service_config['name']}")
    logger.info(f"  Database: {service_config['database']}")
    logger.info(f"  Description: {service_config['description']}")

    if dry_run:
        logger.info(f"  Would run: python {migrate_script}")
        return True

    try:
        result = subprocess.run(
            [sys.executable, str(migrate_script)],
            cwd=service_path,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )

        if result.returncode == 0:
            logger.info(f"✅ {service_config['name']} migration completed successfully")
            if result.stdout.strip():
                logger.debug(f"Migration output:\n{result.stdout}")
            return True
        else:
            logger.error(f"❌ {service_config['name']} migration failed (exit code: {result.returncode})")
            if result.stderr.strip():
                logger.error(f"Migration errors:\n{result.stderr}")
            if result.stdout.strip():
                logger.info(f"Migration output:\n{result.stdout}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"❌ {service_config['name']} migration timed out after 5 minutes")
        return False
    except Exception as e:
        logger.error(f"❌ {service_config['name']} migration failed: {e}")
        return False


def main():
    """Main migration orchestrator"""
    parser = argparse.ArgumentParser(description="Migrate databases for all microservices")
    parser.add_argument(
        "--service",
        choices=list(SERVICES.keys()),
        help="Migrate specific service only"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without running migrations"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    logger = setup_logging()
    repo_root = get_repo_root()

    # Determine which services to migrate
    services_to_migrate = [args.service] if args.service else list(SERVICES.keys())

    logger.info("=" * 60)
    logger.info("Database Migration for BabyAI Microservices")
    logger.info("=" * 60)
    logger.info(f"Repository root: {repo_root}")
    logger.info(f"Services to migrate: {', '.join(services_to_migrate)}")
    logger.info(f"Mode: {'DRY-RUN' if args.dry_run else 'LIVE MIGRATION'}")
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("🔍 DRY-RUN MODE: No actual changes will be made")
    else:
        logger.info("⚠️  LIVE MIGRATION: Database changes will be applied")

    # Run migrations
    success_count = 0
    failed_services = []

    for service_key in services_to_migrate:
        service_config = SERVICES[service_key]

        logger.info(f"\n📦 Processing {service_config['name']}...")

        success = run_service_migration(service_key, service_config, repo_root, args.dry_run)
        if success:
            success_count += 1
        else:
            failed_services.append(service_config['name'])

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("Migration Summary")
    logger.info("=" * 60)
    logger.info(f"Total services processed: {len(services_to_migrate)}")
    logger.info(f"Successful migrations: {success_count}")
    logger.info(f"Failed migrations: {len(failed_services)}")

    if failed_services:
        logger.error(f"Failed services: {', '.join(failed_services)}")
        logger.error("\n❌ Some migrations failed. Check logs above for details.")
        return 1
    else:
        logger.info("\n✅ All migrations completed successfully!")
        logger.info("\nDatabase-per-service pattern implementation complete.")
        logger.info("Each service now has its own isolated database:")
        for service_key in services_to_migrate:
            config = SERVICES[service_key]
            logger.info(f"  • {config['name']}: {config['database']}")
        return 0


if __name__ == "__main__":
    sys.exit(main())