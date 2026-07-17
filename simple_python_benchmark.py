#!/usr/bin/env python3
"""
Simple Python benchmark - File processing simulation
Compares basic file operations to Rust performance
"""

import time
import json
from pathlib import Path
from typing import Dict, Any

def benchmark_python_file_processing(repo_path: Path) -> Dict[str, Any]:
    """Benchmark Python file reading and basic processing"""
    print("Python benchmark: File reading + basic text processing...")

    start_time = time.time()

    # Find source files (same as Rust)
    extensions = {".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go"}
    files = []

    for ext in extensions:
        files.extend(repo_path.rglob(f"*{ext}"))

    # Filter out hidden files
    files = [f for f in files if not str(f).startswith('.')]

    # Simulate symbol counting by processing files
    total_symbols = 0
    file_count = 0

    for file_path in files:
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

                # Simple symbol approximation - count functions/classes
                lines = content.split('\n')
                symbols = 0

                for line in lines:
                    line_stripped = line.strip()
                    if (line_stripped.startswith('def ') or
                        line_stripped.startswith('class ') or
                        line_stripped.startswith('function ') or
                        line_stripped.startswith('const ') or
                        line_stripped.startswith('let ') or
                        line_stripped.startswith('var ') or
                        line_stripped.startswith('fn ') or
                        line_stripped.startswith('func ') or
                        line_stripped.startswith('struct ') or
                        line_stripped.startswith('impl ') or
                        line_stripped.startswith('interface ') or
                        line_stripped.startswith('type ')):
                        symbols += 1

                total_symbols += symbols
                file_count += 1

        except Exception:
            pass  # Skip problematic files

    parse_time = time.time() - start_time

    return {
        "language": "python_simulation",
        "symbols": total_symbols,
        "file_count": file_count,
        "parse_time_ms": int(parse_time * 1000),
        "implementation": "python_file_processing"
    }

def compare_with_rust_results():
    """Compare Python results with Rust results"""
    print("Python vs Rust Performance Comparison")
    print("=" * 50)

    # Run Python benchmark
    repo_path = Path(".")
    python_result = benchmark_python_file_processing(repo_path)

    # Load Rust results
    rust_file = Path("repograph-poc/rust_parse_results.json")
    if not rust_file.exists():
        print("[ERROR] Rust results file not found. Run Rust POC first.")
        return

    with open(rust_file, 'r') as f:
        rust_result = json.load(f)

    # Extract symbol count from Rust results
    rust_symbol_count = len(rust_result['symbols']) if isinstance(rust_result['symbols'], list) else rust_result['symbols']

    # Print comparison
    print(f"\nResults:")
    print(f"Python: {python_result['file_count']} files, {python_result['symbols']} symbols, {python_result['parse_time_ms']}ms")
    print(f"Rust:   {rust_result['file_count']} files, {rust_symbol_count} symbols, {rust_result['parse_time_ms']}ms")

    # Calculate speedup
    if python_result['parse_time_ms'] > 0:
        speedup = python_result['parse_time_ms'] / rust_result['parse_time_ms']
        print(f"\nRust speedup: {speedup:.2f}x faster than Python")

        # Performance metrics
        py_fps = python_result['file_count'] / (python_result['parse_time_ms'] / 1000)
        rust_fps = rust_result['file_count'] / (rust_result['parse_time_ms'] / 1000)

        py_sps = python_result['symbols'] / (python_result['parse_time_ms'] / 1000)
        rust_sps = rust_symbol_count / (rust_result['parse_time_ms'] / 1000)

        print(f"\nThroughput comparison:")
        print(f"Files/sec:   Python={py_fps:.1f}, Rust={rust_fps:.1f} ({rust_fps/py_fps:.1f}x)")
        print(f"Symbols/sec: Python={py_sps:.1f}, Rust={rust_sps:.1f} ({rust_sps/py_sps:.1f}x)")

    # Save comparison (create clean rust result without full symbols array)
    rust_summary = {
        "file_count": rust_result['file_count'],
        "symbols": rust_symbol_count,
        "parse_time_ms": rust_result['parse_time_ms'],
        "language": rust_result['language']
    }

    comparison = {
        "python": python_result,
        "rust": rust_summary,
        "speedup": speedup,
        "test_date": "2026-05-20"
    }

    with open("benchmark_comparison_results.json", 'w') as f:
        json.dump(comparison, f, indent=2)

    print(f"\nComparison saved to: benchmark_comparison_results.json")

    return comparison

if __name__ == "__main__":
    results = compare_with_rust_results()

    if results:
        speedup = results["speedup"]
        print(f"\nConclusion: Rust is {speedup:.1f}x faster than Python for repo parsing!")

        if speedup >= 10:
            print("[EXCELLENT] 10x+ speedup achieved!")
        elif speedup >= 5:
            print("[GOOD] 5x+ speedup achieved")
        elif speedup >= 2:
            print("[FAIR] 2x+ speedup achieved")
        else:
            print("[WARNING] Rust speedup lower than expected")