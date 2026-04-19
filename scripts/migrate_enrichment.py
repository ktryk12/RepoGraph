"""
Fase 1 enrichment migration — re-indexes the existing graph to add:
  service_name, belongs_to_service, is_test, is_entrypoint, risk_level, signature

ESCALATION: Running this against 47k+ nodes requires migration_strategy_decision approval.
Do NOT run in production without confirming with the repo owner.

Usage:
    python scripts/migrate_enrichment.py [--repo-path PATH] [--db-path PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from repograph.graph import get_graph_store
from repograph.indexer import parse_file, walk
from repograph.indexer.config_indexer import index_config_file, walk_config_files


def migrate(repo_path: str, db_path: str, dry_run: bool = False) -> None:
    print(f"[migrate] repo={repo_path} db={db_path} dry_run={dry_run}")
    repo_root = Path(repo_path).expanduser().resolve()
    store = get_graph_store(backend="cog", db_path=db_path)

    start = time.perf_counter()
    files_processed = 0
    triples_added = 0

    # Re-parse source files to pick up new enrichment triples
    for filepath, language in walk(str(repo_root)):
        new_triples = parse_file(filepath, language, repo_path=repo_root)
        if new_triples and not dry_run:
            store.put_triples_batch(new_triples)
        triples_added += len(new_triples)
        files_processed += 1
        if files_processed % 100 == 0:
            print(f"  {files_processed} files processed, {triples_added} triples...")

    # Index config files
    for config_path, _ in walk_config_files(str(repo_root)):
        config_triples = index_config_file(config_path, repo_root)
        if config_triples and not dry_run:
            store.put_triples_batch(config_triples)
        triples_added += len(config_triples)

    duration = time.perf_counter() - start
    print(f"[migrate] done: {files_processed} files, {triples_added} triples in {duration:.1f}s")
    if dry_run:
        print("[migrate] DRY RUN — no changes written")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fase 1 enrichment migration")
    parser.add_argument("--repo-path", default=".", help="Path to repository to re-index")
    parser.add_argument("--db-path", default=".repograph", help="Graph store path")
    parser.add_argument("--dry-run", action="store_true", help="Parse but do not write")
    args = parser.parse_args()
    migrate(args.repo_path, args.db_path, dry_run=args.dry_run)
