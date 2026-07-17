#!/usr/bin/env python3
"""
RepoGraph Performance Benchmarking Framework
Comprehensive performance comparison: Rust vs Python implementation

Benchmarks:
- Repository indexing speed
- Symbol search performance
- API response times
- Memory usage
- Concurrent request handling
- Large repository scaling
"""

import json
import time
import psutil
import requests
import subprocess
import threading
import statistics
from typing import Dict, List, Any, Tuple, Optional
from pathlib import Path
import tempfile
import csv
from dataclasses import dataclass, asdict
import concurrent.futures

@dataclass
class BenchmarkResult:
    test_name: str
    rust_time: float
    python_time: Optional[float]
    rust_memory_mb: float
    python_memory_mb: Optional[float]
    rust_success: bool
    python_success: bool
    speedup_factor: Optional[float]
    additional_metrics: Dict[str, Any]

class PerformanceBenchmark:
    def __init__(self):
        self.results: List[BenchmarkResult] = []
        self.test_repo_path = Path("..").resolve()
        self.temp_db_path = None

    def setup_temp_database(self) -> Path:
        """Create temporary database for testing"""
        if self.temp_db_path:
            return self.temp_db_path

        self.temp_db_path = Path(tempfile.mkdtemp()) / "benchmark_db"
        return self.temp_db_path

    def cleanup_temp_database(self):
        """Clean up temporary database"""
        if self.temp_db_path and self.temp_db_path.exists():
            import shutil
            shutil.rmtree(self.temp_db_path.parent)
            self.temp_db_path = None

    def measure_memory_usage(self, process: psutil.Process) -> float:
        """Measure memory usage in MB"""
        try:
            memory_info = process.memory_info()
            return memory_info.rss / (1024 * 1024)  # Convert to MB
        except:
            return 0.0

    def benchmark_repository_indexing(self) -> BenchmarkResult:
        """Benchmark repository indexing performance"""
        print("Benchmarking repository indexing...")

        # Test Rust implementation
        rust_time, rust_memory, rust_success = self.benchmark_rust_indexing()

        # Test Python implementation (if available)
        python_time, python_memory, python_success = self.benchmark_python_indexing()

        speedup = python_time / rust_time if python_time and rust_time > 0 else None

        return BenchmarkResult(
            test_name="repository_indexing",
            rust_time=rust_time,
            python_time=python_time,
            rust_memory_mb=rust_memory,
            python_memory_mb=python_memory,
            rust_success=rust_success,
            python_success=python_success,
            speedup_factor=speedup,
            additional_metrics={
                "repo_size_files": self.count_source_files(),
                "repo_size_lines": self.count_source_lines()
            }
        )

    def benchmark_rust_indexing(self) -> Tuple[float, float, bool]:
        """Benchmark Rust repository indexing"""
        db_path = self.setup_temp_database()

        cmd = [
            "repograph-poc/target/release/repograph-poc.exe",
            "--repo-path", str(self.test_repo_path),
            "--benchmark", "--parallel",
            "--db-path", str(db_path)
        ]

        start_time = time.time()
        initial_memory = self.get_system_memory()

        try:
            process = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            end_time = time.time()

            final_memory = self.get_system_memory()
            memory_used = max(0, final_memory - initial_memory)

            success = process.returncode == 0
            execution_time = end_time - start_time

            return execution_time, memory_used, success

        except subprocess.TimeoutExpired:
            return 300.0, 0.0, False
        except Exception:
            return 0.0, 0.0, False

    def benchmark_python_indexing(self) -> Tuple[Optional[float], Optional[float], bool]:
        """Benchmark Python repository indexing (if available)"""
        # This would test the Python RepoGraph if available
        # For now, return None since we don't have Python implementation running
        return None, None, False

    def benchmark_api_performance(self) -> List[BenchmarkResult]:
        """Benchmark API endpoint performance"""
        print("Benchmarking API performance...")

        # Start Rust API server
        rust_process = self.start_rust_api_server()
        if not rust_process:
            return []

        results = []

        try:
            # Wait for server to be ready
            self.wait_for_api_server("http://127.0.0.1:8001")

            # Test various endpoints
            endpoints = [
                ("/status", "GET", None),
                ("/symbols?q=parse&limit=10", "GET", None),
                ("/symbols?q=function&limit=20", "GET", None),
                ("/symbols?q=class&limit=5", "GET", None),
            ]

            for endpoint, method, data in endpoints:
                result = self.benchmark_api_endpoint(endpoint, method, data)
                results.append(result)

        finally:
            if rust_process:
                rust_process.terminate()
                rust_process.wait(timeout=5)

        return results

    def benchmark_api_endpoint(self, endpoint: str, method: str,
                              data: Optional[Dict]) -> BenchmarkResult:
        """Benchmark a specific API endpoint"""
        url = f"http://127.0.0.1:8001{endpoint}"

        # Warmup requests
        for _ in range(3):
            try:
                if method == "GET":
                    requests.get(url, timeout=5)
                else:
                    requests.post(url, json=data, timeout=5)
            except:
                pass

        # Actual benchmark
        times = []
        successes = 0

        for _ in range(10):
            start_time = time.time()
            try:
                if method == "GET":
                    response = requests.get(url, timeout=5)
                else:
                    response = requests.post(url, json=data, timeout=5)

                end_time = time.time()
                times.append(end_time - start_time)

                if response.status_code == 200:
                    successes += 1

            except:
                times.append(5.0)  # Timeout time

        avg_time = statistics.mean(times) if times else 0.0
        min_time = min(times) if times else 0.0
        max_time = max(times) if times else 0.0
        success_rate = successes / 10

        return BenchmarkResult(
            test_name=f"api_{method.lower()}_{endpoint.replace('/', '_').replace('?', '_').replace('&', '_')}",
            rust_time=avg_time,
            python_time=None,
            rust_memory_mb=0.0,
            python_memory_mb=None,
            rust_success=success_rate > 0.8,
            python_success=False,
            speedup_factor=None,
            additional_metrics={
                "min_time": min_time,
                "max_time": max_time,
                "success_rate": success_rate,
                "requests_tested": 10
            }
        )

    def benchmark_concurrent_load(self) -> BenchmarkResult:
        """Benchmark concurrent request handling"""
        print("Benchmarking concurrent load handling...")

        rust_process = self.start_rust_api_server()
        if not rust_process:
            return self.create_failed_result("concurrent_load")

        try:
            self.wait_for_api_server("http://127.0.0.1:8001")

            # Test different concurrency levels
            concurrency_levels = [1, 5, 10, 20, 50]
            results = {}

            for concurrency in concurrency_levels:
                print(f"Testing {concurrency} concurrent requests...")

                def make_request():
                    start = time.time()
                    try:
                        response = requests.get(
                            "http://127.0.0.1:8001/symbols?q=test&limit=5",
                            timeout=10
                        )
                        return time.time() - start, response.status_code == 200
                    except:
                        return time.time() - start, False

                start_time = time.time()

                with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
                    futures = [executor.submit(make_request) for _ in range(concurrency * 2)]
                    request_results = [f.result() for f in concurrent.futures.as_completed(futures)]

                total_time = time.time() - start_time
                times, successes = zip(*request_results)

                avg_response_time = statistics.mean(times)
                success_rate = sum(successes) / len(successes)
                requests_per_second = len(request_results) / total_time

                results[f"concurrency_{concurrency}"] = {
                    "avg_response_time": avg_response_time,
                    "success_rate": success_rate,
                    "requests_per_second": requests_per_second
                }

            # Find optimal concurrency
            best_rps = max(results.values(), key=lambda x: x["requests_per_second"])

            return BenchmarkResult(
                test_name="concurrent_load",
                rust_time=best_rps["avg_response_time"],
                python_time=None,
                rust_memory_mb=0.0,
                python_memory_mb=None,
                rust_success=best_rps["success_rate"] > 0.9,
                python_success=False,
                speedup_factor=None,
                additional_metrics=results
            )

        finally:
            if rust_process:
                rust_process.terminate()
                rust_process.wait(timeout=5)

    def start_rust_api_server(self) -> Optional[subprocess.Popen]:
        """Start Rust API server for testing"""
        cmd = [
            "repograph-poc/target/release/repograph-api.exe",
            "--host", "127.0.0.1",
            "--port", "8001",
            "--preload-repo", str(self.test_repo_path)
        ]

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return process
        except:
            return None

    def wait_for_api_server(self, url: str, timeout: int = 30):
        """Wait for API server to be ready"""
        for _ in range(timeout):
            try:
                requests.get(f"{url}/status", timeout=1)
                return
            except:
                time.sleep(1)
        raise Exception("API server failed to start")

    def count_source_files(self) -> int:
        """Count source files in repository"""
        extensions = {".py", ".js", ".ts", ".rs", ".go", ".java", ".cpp", ".c", ".h"}
        count = 0
        for ext in extensions:
            count += len(list(self.test_repo_path.rglob(f"*{ext}")))
        return count

    def count_source_lines(self) -> int:
        """Estimate source lines of code"""
        extensions = {".py", ".js", ".ts", ".rs", ".go"}
        lines = 0
        for ext in extensions:
            for file_path in self.test_repo_path.rglob(f"*{ext}"):
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        lines += sum(1 for _ in f)
                except:
                    pass
        return lines

    def get_system_memory(self) -> float:
        """Get current system memory usage in MB"""
        return psutil.virtual_memory().used / (1024 * 1024)

    def create_failed_result(self, test_name: str) -> BenchmarkResult:
        """Create a failed benchmark result"""
        return BenchmarkResult(
            test_name=test_name,
            rust_time=0.0,
            python_time=None,
            rust_memory_mb=0.0,
            python_memory_mb=None,
            rust_success=False,
            python_success=False,
            speedup_factor=None,
            additional_metrics={"error": "Test failed to run"}
        )

    def run_all_benchmarks(self) -> List[BenchmarkResult]:
        """Run all performance benchmarks"""
        print("RepoGraph Performance Benchmark Suite")
        print("=" * 50)

        all_results = []

        try:
            # Repository indexing benchmark
            result = self.benchmark_repository_indexing()
            all_results.append(result)
            self.print_result(result)

            # API performance benchmarks
            api_results = self.benchmark_api_performance()
            all_results.extend(api_results)
            for result in api_results:
                self.print_result(result)

            # Concurrent load benchmark
            load_result = self.benchmark_concurrent_load()
            all_results.append(load_result)
            self.print_result(load_result)

        finally:
            self.cleanup_temp_database()

        return all_results

    def print_result(self, result: BenchmarkResult):
        """Print a single benchmark result"""
        status = "PASS" if result.rust_success else "FAIL"
        print(f"\n{result.test_name}: {status}")
        print(f"  Rust time: {result.rust_time:.3f}s")
        if result.python_time:
            print(f"  Python time: {result.python_time:.3f}s")
            print(f"  Speedup: {result.speedup_factor:.2f}x")
        if result.rust_memory_mb > 0:
            print(f"  Memory: {result.rust_memory_mb:.1f}MB")

    def save_results(self, results: List[BenchmarkResult], filename: str = "benchmark_results.json"):
        """Save results to JSON file"""
        data = [asdict(result) for result in results]
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"\nResults saved to {filename}")

    def generate_report(self, results: List[BenchmarkResult]) -> str:
        """Generate comprehensive benchmark report"""
        if not results:
            return "No benchmark results available"

        successful_tests = [r for r in results if r.rust_success]
        total_tests = len(results)
        success_rate = len(successful_tests) / total_tests

        # Calculate aggregate metrics
        avg_response_time = statistics.mean([r.rust_time for r in successful_tests])

        report_lines = [
            "RepoGraph Rust Performance Benchmark Report",
            "=" * 60,
            f"Tests run: {total_tests}",
            f"Success rate: {success_rate:.1%}",
            f"Average response time: {avg_response_time:.3f}s",
            "",
            "Individual Test Results:",
            "-" * 40
        ]

        for result in results:
            status = "PASS" if result.rust_success else "FAIL"
            report_lines.append(
                f"{result.test_name}: {status} ({result.rust_time:.3f}s)"
            )

        return "\n".join(report_lines)

def main():
    """Main benchmark execution"""
    benchmark = PerformanceBenchmark()
    results = benchmark.run_all_benchmarks()

    # Save results
    benchmark.save_results(results)

    # Generate and print report
    report = benchmark.generate_report(results)
    print(f"\n{report}")

    # Determine overall success
    success_rate = sum(1 for r in results if r.rust_success) / len(results) if results else 0
    overall_success = success_rate >= 0.8

    print(f"\nOverall Performance Assessment: {'PASS' if overall_success else 'FAIL'}")
    return overall_success

if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)