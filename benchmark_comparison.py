#!/usr/bin/env python3
"""
Benchmark comparison script: Python vs Rust Tree-sitter parsing
Compares RepoGraph's current Python implementation with the Rust POC
"""

import json
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Any
import tree_sitter_python as tspython
import tree_sitter
from repograph.parser.tree_sitter_parser import TreeSitterParser


def benchmark_python_parsing(repo_path: Path) -> Dict[str, Any]:
    """Benchmark current Python Tree-sitter implementation"""
    print("🐍 Running Python benchmark...")

    start_time = time.time()

    # Use existing RepoGraph parser
    parser = TreeSitterParser()
    results = []
    file_count = 0

    # Find source files
    for file_path in repo_path.rglob("*.py"):
        if file_path.is_file() and not str(file_path).startswith('.'):
            try:
                symbols = parser.parse_file(str(file_path))
                results.extend(symbols)
                file_count += 1
            except Exception as e:
                print(f"Error parsing {file_path}: {e}")

    parse_time = time.time() - start_time

    return {
        "language": "python",
        "symbols": len(results),
        "file_count": file_count,
        "parse_time_ms": int(parse_time * 1000),
        "implementation": "current_python"
    }


def benchmark_rust_parsing(repo_path: Path) -> Dict[str, Any]:
    """Benchmark Rust POC implementation"""
    print("🦀 Running Rust benchmark...")

    # Build Rust POC first
    print("Building Rust POC...")
    build_result = subprocess.run(
        ["cargo", "build", "--release"],
        cwd="repograph-poc",
        capture_output=True,
        text=True
    )

    if build_result.returncode != 0:
        print(f"Rust build failed: {build_result.stderr}")
        return {"error": "build_failed", "stderr": build_result.stderr}

    # Run Rust benchmark
    rust_result = subprocess.run(
        ["cargo", "run", "--release", "--",
         "--repo-path", str(repo_path),
         "--benchmark", "--parallel"],
        cwd="repograph-poc",
        capture_output=True,
        text=True
    )

    if rust_result.returncode != 0:
        print(f"Rust execution failed: {rust_result.stderr}")
        return {"error": "execution_failed", "stderr": rust_result.stderr}

    # Parse results from JSON file
    try:
        results_file = Path("repograph-poc/rust_parse_results.json")
        if results_file.exists():
            with open(results_file, 'r') as f:
                rust_data = json.load(f)
            rust_data["implementation"] = "rust_poc"
            return rust_data
        else:
            return {"error": "results_file_missing"}
    except Exception as e:
        return {"error": f"json_parse_failed: {e}"}


def run_comparison_benchmark(repo_paths: List[Path]):
    """Run complete comparison benchmark"""
    print("🔥 RepoGraph Python vs Rust Benchmark")
    print("=" * 50)

    results = []

    for repo_path in repo_paths:
        if not repo_path.exists():
            print(f"❌ Repo path not found: {repo_path}")
            continue

        print(f"\n📂 Testing repository: {repo_path.name}")

        # Python benchmark
        python_result = benchmark_python_parsing(repo_path)

        # Rust benchmark
        rust_result = benchmark_rust_parsing(repo_path)

        # Calculate performance comparison
        if "error" not in rust_result and "error" not in python_result:
            speedup = python_result["parse_time_ms"] / rust_result["parse_time_ms"]

            comparison = {
                "repo": str(repo_path),
                "python": python_result,
                "rust": rust_result,
                "speedup": round(speedup, 2),
                "rust_faster": speedup > 1.0
            }

            results.append(comparison)

            # Print immediate results
            print(f"\n📊 Results for {repo_path.name}:")
            print(f"  Python: {python_result['file_count']} files, {python_result['symbols']} symbols, {python_result['parse_time_ms']}ms")
            print(f"  Rust:   {rust_result['file_count']} files, {rust_result['symbols']} symbols, {rust_result['parse_time_ms']}ms")
            print(f"  🚀 Speedup: {speedup:.2f}x {'faster' if speedup > 1 else 'slower'}")
        else:
            print(f"❌ Benchmark failed for {repo_path}")
            if "error" in python_result:
                print(f"   Python error: {python_result['error']}")
            if "error" in rust_result:
                print(f"   Rust error: {rust_result['error']}")

    # Save comprehensive results
    output_file = "benchmark_results.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\n💾 Detailed results saved to: {output_file}")

    # Summary
    if results:
        avg_speedup = sum(r["speedup"] for r in results) / len(results)
        print(f"\n🎯 Summary:")
        print(f"  Repositories tested: {len(results)}")
        print(f"  Average speedup: {avg_speedup:.2f}x")
        print(f"  Rust wins: {sum(1 for r in results if r['rust_faster'])}/{len(results)}")

    return results


if __name__ == "__main__":
    import sys

    # Test repositories
    test_repos = [
        Path("."),  # RepoGraph itself
    ]

    # Add additional repos from command line
    for arg in sys.argv[1:]:
        test_repos.append(Path(arg))

    print("🔬 Starting Python vs Rust parsing benchmark...")
    results = run_comparison_benchmark(test_repos)

    if not results:
        print("\n❌ No successful benchmarks completed")
        sys.exit(1)
    else:
        print("\n✅ Benchmark completed successfully!")