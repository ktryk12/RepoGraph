"""
RepoGraph benchmark suite — measures retrieval precision against a live graph.

Usage:
    python scripts/benchmark.py [--db-path PATH] [--output results.json]

Metrics tracked (per PROGRAM_REPOGRAPH section 8):
    - token_estimate per query (vs. flat retrieval baseline)
    - stage counts (coarse → structural → fine)
    - task family classification accuracy
    - duration_ms per query
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from repograph.graph import get_graph_store
from repograph.retrieval.pipeline import retrieve
from repograph.retrieval.task_planner import classify

# Benchmark scenarios: (query, expected_task_family, expected_symbols_substring)
SCENARIOS = [
    ("where is RepoGraph defined",            "symbol_lookup",           "RepoGraph"),
    ("what calls parse_file",                 "call_chain_reasoning",    "parse_file"),
    ("blast radius of put_triple",            "blast_radius_analysis",   "put_triple"),
    ("find the graph store",                  "symbol_lookup",           "store"),
    ("what tests cover the walker",           "test_impact_lookup",      "walker"),
    ("generate tests for blast_radius",       "targeted_test_generation","blast_radius"),
    ("bug in the indexer parser",             "bug_localization",        "parser"),
    ("refactor IN_FILE to in_file",           "targeted_refactor",       "IN_FILE"),
    ("what config affects the api server",    "config_dependency_reasoning", ""),
    ("symbols in repograph/graph/store.py",   "file_to_symbol_map",      "store"),
]

_FLAT_RETRIEVAL_TOKEN_ESTIMATE = 4096  # baseline: whole repo dumped


def run_benchmarks(db_path: str) -> dict:
    store = get_graph_store(backend="cog", db_path=db_path)
    results = []
    total_tokens = 0
    classify_correct = 0

    for query, expected_family, expected_symbol_hint in SCENARIOS:
        t0 = time.perf_counter()
        predicted_family = classify(query)
        result = retrieve(query, store, persist_trace=False)
        duration_ms = int((time.perf_counter() - t0) * 1000)

        family_correct = predicted_family == expected_family
        symbol_hit = any(
            expected_symbol_hint.lower() in s["symbol"].lower()
            for s in result.working_set
        ) if expected_symbol_hint else True

        if family_correct:
            classify_correct += 1
        total_tokens += result.token_estimate

        results.append({
            "query": query,
            "expected_family": expected_family,
            "predicted_family": predicted_family,
            "family_correct": family_correct,
            "symbol_hit": symbol_hit,
            "stages": result.stages,
            "token_estimate": result.token_estimate,
            "duration_ms": duration_ms,
        })

    n = len(SCENARIOS)
    avg_tokens = total_tokens // n if n else 0
    token_reduction_pct = round((1 - avg_tokens / _FLAT_RETRIEVAL_TOKEN_ESTIMATE) * 100, 1)
    classify_accuracy = round(classify_correct / n * 100, 1)

    summary = {
        "scenarios": n,
        "classify_accuracy_pct": classify_accuracy,
        "avg_token_estimate": avg_tokens,
        "flat_retrieval_baseline": _FLAT_RETRIEVAL_TOKEN_ESTIMATE,
        "token_reduction_pct": token_reduction_pct,
        "target_token_reduction_pct": 50,
        "token_reduction_meets_target": token_reduction_pct >= 50,
        "results": results,
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="RepoGraph benchmark suite")
    parser.add_argument("--db-path", default=".repograph")
    parser.add_argument("--output", default=None, help="Write JSON results to file")
    args = parser.parse_args()

    print(f"Running {len(SCENARIOS)} benchmark scenarios against {args.db_path}...")
    summary = run_benchmarks(args.db_path)

    print(f"\n{'='*55}")
    print(f"  Classify accuracy:   {summary['classify_accuracy_pct']}%")
    print(f"  Avg token estimate:  {summary['avg_token_estimate']} (baseline: {summary['flat_retrieval_baseline']})")
    print(f"  Token reduction:     {summary['token_reduction_pct']}% (target: >=50%)")
    print(f"  Meets target:        {'YES' if summary['token_reduction_meets_target'] else 'NO'}")
    print(f"{'='*55}\n")

    for r in summary["results"]:
        ok = "OK" if r["family_correct"] else "XX"
        hit = "HIT" if r["symbol_hit"] else "  -"
        print(f"  {ok} {hit}  [{r['predicted_family']:<35}] {r['query'][:45]}")

    if args.output:
        Path(args.output).write_text(json.dumps(summary, indent=2))
        print(f"\nResults written to {args.output}")


if __name__ == "__main__":
    main()
