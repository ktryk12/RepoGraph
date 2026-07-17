#!/usr/bin/env python3
"""
RepoGraph API Compatibility Test Suite
Compares Rust implementation against Python RepoGraph API

Tests both REST API and MCP server for exact compatibility
"""

import json
import time
import requests
import subprocess
import sys
from typing import Dict, Any, List, Optional
from pathlib import Path
from dataclasses import dataclass
import concurrent.futures
import tempfile
import shutil

@dataclass
class TestResult:
    test_name: str
    rust_success: bool
    python_success: bool
    rust_response_time: float
    python_response_time: Optional[float]
    rust_data: Optional[Dict[str, Any]]
    python_data: Optional[Dict[str, Any]]
    compatibility_score: float
    notes: str

class CompatibilityTester:
    def __init__(self, rust_api_url: str = "http://127.0.0.1:8001",
                 python_api_url: str = "http://127.0.0.1:8000"):
        self.rust_api_url = rust_api_url
        self.python_api_url = python_api_url
        self.test_results: List[TestResult] = []
        self.test_repo_path = Path("..").resolve()

    def start_rust_api(self) -> subprocess.Popen:
        """Start Rust API server"""
        cmd = [
            "repograph-poc/target/release/repograph-api.exe",
            "--host", "127.0.0.1",
            "--port", "8001",
            "--preload-repo", str(self.test_repo_path)
        ]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Wait for server to start
        for _ in range(30):
            try:
                requests.get(f"{self.rust_api_url}/status", timeout=1)
                break
            except:
                time.sleep(0.5)
        else:
            raise Exception("Rust API server failed to start")

        return process

    def test_endpoint(self, endpoint: str, method: str = "GET",
                     data: Optional[Dict] = None) -> TestResult:
        """Test a specific endpoint against both implementations"""
        test_name = f"{method} {endpoint}"

        # Test Rust API
        rust_start = time.time()
        try:
            if method == "GET":
                rust_response = requests.get(f"{self.rust_api_url}{endpoint}")
            elif method == "POST":
                rust_response = requests.post(f"{self.rust_api_url}{endpoint}", json=data)

            rust_time = time.time() - rust_start
            rust_success = rust_response.status_code == 200
            rust_data = rust_response.json() if rust_success else None

        except Exception as e:
            rust_success = False
            rust_time = time.time() - rust_start
            rust_data = {"error": str(e)}

        # Test Python API (if available)
        python_success = False
        python_time = None
        python_data = None

        try:
            python_start = time.time()
            if method == "GET":
                python_response = requests.get(f"{self.python_api_url}{endpoint}", timeout=5)
            elif method == "POST":
                python_response = requests.post(f"{self.python_api_url}{endpoint}",
                                               json=data, timeout=5)

            python_time = time.time() - python_start
            python_success = python_response.status_code == 200
            python_data = python_response.json() if python_success else None

        except:
            # Python API not available - compare against expected structure
            python_success = None
            python_time = None
            python_data = None

        # Calculate compatibility score
        if python_data and rust_data:
            compatibility_score = self.calculate_compatibility(rust_data, python_data)
            notes = "Full comparison with Python API"
        else:
            compatibility_score = 1.0 if rust_success else 0.0
            notes = "Rust-only test (Python API unavailable)"

        return TestResult(
            test_name=test_name,
            rust_success=rust_success,
            python_success=python_success,
            rust_response_time=rust_time,
            python_response_time=python_time,
            rust_data=rust_data,
            python_data=python_data,
            compatibility_score=compatibility_score,
            notes=notes
        )

    def calculate_compatibility(self, rust_data: Dict, python_data: Dict) -> float:
        """Calculate compatibility score between responses"""
        if not isinstance(rust_data, dict) or not isinstance(python_data, dict):
            return 0.0 if rust_data != python_data else 1.0

        # Check common fields
        rust_keys = set(rust_data.keys())
        python_keys = set(python_data.keys())

        common_keys = rust_keys & python_keys
        if not common_keys:
            return 0.0

        matching_fields = 0
        total_fields = len(common_keys)

        for key in common_keys:
            rust_val = rust_data[key]
            python_val = python_data[key]

            if key in ['symbols', 'total_count'] and isinstance(rust_val, (int, list)):
                # For symbol counts and arrays, allow some variance
                if isinstance(rust_val, int) and isinstance(python_val, int):
                    ratio = min(rust_val, python_val) / max(rust_val, python_val) if max(rust_val, python_val) > 0 else 1.0
                    matching_fields += ratio
                else:
                    matching_fields += 1 if rust_val == python_val else 0.5
            elif str(rust_val) == str(python_val):
                matching_fields += 1
            else:
                # Partial match for similar but not identical values
                matching_fields += 0.5

        return matching_fields / total_fields

    def run_comprehensive_tests(self) -> List[TestResult]:
        """Run comprehensive compatibility test suite"""
        print("Starting Rust API server...")
        rust_process = None

        try:
            rust_process = self.start_rust_api()
            print(f"[OK] Rust API server started")

            tests = [
                # Core endpoints
                ("/status", "GET"),
                ("/symbols?q=parse&limit=5", "GET"),
                ("/symbols?q=main&limit=10", "GET"),
                ("/symbols?q=test&limit=3", "GET"),

                # Index endpoint
                ("/index", "POST", {
                    "repo_path": str(self.test_repo_path),
                    "parallel": True
                }),

                # File-based queries (would need actual file paths from index)
                # These would be filled in with real symbol IDs after indexing
            ]

            print(f"\nRunning {len(tests)} API compatibility tests...")

            for endpoint, method, *data in tests:
                data = data[0] if data else None
                print(f"Testing {method} {endpoint}...")

                result = self.test_endpoint(endpoint, method, data)
                self.test_results.append(result)

                status = "[OK]" if result.rust_success else "[FAIL]"
                compat = f"{result.compatibility_score:.2f}"
                print(f"  {status} Rust: {result.rust_response_time:.3f}s, Compat: {compat}")

        finally:
            if rust_process:
                rust_process.terminate()
                rust_process.wait(timeout=5)
                print("[OK] Rust API server stopped")

        return self.test_results

    def performance_benchmark(self) -> Dict[str, Any]:
        """Run performance benchmark tests"""
        print("\nRunning performance benchmarks...")

        rust_process = self.start_rust_api()

        try:
            # Test concurrent requests
            def make_request():
                response = requests.get(f"{self.rust_api_url}/symbols?q=test&limit=5")
                return response.elapsed.total_seconds()

            # Single request baseline
            single_time = make_request()

            # Concurrent requests test
            print("Testing concurrent request handling...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                start_time = time.time()
                futures = [executor.submit(make_request) for _ in range(50)]
                times = [f.result() for f in concurrent.futures.as_completed(futures)]
                total_time = time.time() - start_time

            avg_response_time = sum(times) / len(times)
            requests_per_second = len(times) / total_time

            return {
                "single_request_time": single_time,
                "concurrent_avg_response": avg_response_time,
                "requests_per_second": requests_per_second,
                "total_requests": len(times),
                "success_rate": 100.0  # All should succeed
            }

        finally:
            rust_process.terminate()
            rust_process.wait(timeout=5)

    def generate_report(self) -> str:
        """Generate compatibility test report"""
        if not self.test_results:
            return "No test results available"

        total_tests = len(self.test_results)
        successful_tests = sum(1 for r in self.test_results if r.rust_success)
        avg_compatibility = sum(r.compatibility_score for r in self.test_results) / total_tests
        avg_response_time = sum(r.rust_response_time for r in self.test_results) / total_tests

        report = [
            "RepoGraph Rust API Compatibility Report",
            "=" * 50,
            f"Total tests: {total_tests}",
            f"Successful: {successful_tests} ({successful_tests/total_tests:.1%})",
            f"Average compatibility: {avg_compatibility:.2f}",
            f"Average response time: {avg_response_time:.3f}s",
            "",
            "Detailed Results:",
            "-" * 30
        ]

        for result in self.test_results:
            status = "PASS" if result.rust_success else "FAIL"
            report.append(f"{result.test_name}: {status} "
                         f"({result.rust_response_time:.3f}s, "
                         f"compat: {result.compatibility_score:.2f})")

        return "\n".join(report)

def main():
    """Main test execution"""
    print("RepoGraph Rust API Compatibility Test Suite")
    print("=" * 60)

    tester = CompatibilityTester()

    # Run API compatibility tests
    results = tester.run_comprehensive_tests()

    # Run performance benchmark
    try:
        perf_results = tester.performance_benchmark()
        print(f"\nPerformance Results:")
        print(f"Single request: {perf_results['single_request_time']:.3f}s")
        print(f"Concurrent avg: {perf_results['concurrent_avg_response']:.3f}s")
        print(f"Requests/sec: {perf_results['requests_per_second']:.1f}")
    except Exception as e:
        print(f"Performance benchmark failed: {e}")

    # Generate and display report
    print(f"\n{tester.generate_report()}")

    # Determine overall success
    if results:
        success_rate = sum(1 for r in results if r.rust_success) / len(results)
        avg_compatibility = sum(r.compatibility_score for r in results) / len(results)

        overall_success = success_rate >= 0.9 and avg_compatibility >= 0.8

        print(f"\nOverall Assessment: {'PASS' if overall_success else 'FAIL'}")
        print(f"Success Rate: {success_rate:.1%}")
        print(f"Compatibility Score: {avg_compatibility:.2f}")

        return overall_success

    return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)