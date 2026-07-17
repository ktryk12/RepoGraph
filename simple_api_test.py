#!/usr/bin/env python3
"""
Simple API compatibility test for running RepoGraph Rust API server
Tests basic endpoints and measures performance
"""

import requests
import time
import json
import statistics
from typing import Dict, Any, List

def test_api_endpoint(url: str, method: str = "GET", data: Dict = None) -> Dict[str, Any]:
    """Test a single API endpoint and return metrics"""
    times = []
    successful_requests = 0

    # Run multiple requests to get average performance
    for _ in range(5):
        start_time = time.time()
        try:
            if method == "GET":
                response = requests.get(url, timeout=10)
            else:
                response = requests.post(url, json=data, timeout=10)

            end_time = time.time()
            times.append(end_time - start_time)

            if response.status_code == 200:
                successful_requests += 1
                response_data = response.json()
            else:
                response_data = {"error": f"HTTP {response.status_code}"}

        except Exception as e:
            times.append(10.0)  # Timeout
            response_data = {"error": str(e)}

    return {
        "url": url,
        "method": method,
        "avg_response_time": statistics.mean(times) if times else 0,
        "min_response_time": min(times) if times else 0,
        "max_response_time": max(times) if times else 0,
        "success_rate": successful_requests / 5,
        "last_response": response_data if 'response_data' in locals() else None
    }

def run_api_compatibility_tests():
    """Run comprehensive API compatibility tests"""
    print("RepoGraph Rust API Compatibility Test")
    print("=" * 50)

    base_url = "http://127.0.0.1:8001"

    # Define test cases
    test_cases = [
        # Core endpoints
        (f"{base_url}/status", "GET"),

        # Search endpoints with different queries
        (f"{base_url}/symbols?q=main&limit=5", "GET"),
        (f"{base_url}/symbols?q=parse&limit=10", "GET"),
        (f"{base_url}/symbols?q=class&limit=3", "GET"),
        (f"{base_url}/symbols?q=function&limit=15", "GET"),
        (f"{base_url}/symbols?q=test&limit=8", "GET"),

        # Index endpoint
        (f"{base_url}/index", "POST", {"repo_path": "..", "parallel": True}),
    ]

    results = []

    print(f"\nRunning {len(test_cases)} API tests...")

    for i, test_case in enumerate(test_cases, 1):
        url = test_case[0]
        method = test_case[1]
        data = test_case[2] if len(test_case) > 2 else None

        print(f"\n[{i}/{len(test_cases)}] Testing {method} {url.replace(base_url, '')}...")

        result = test_api_endpoint(url, method, data)
        results.append(result)

        # Print immediate feedback
        status = "OK" if result["success_rate"] >= 0.8 else "FAIL"
        print(f"  Status: {status}")
        print(f"  Avg response: {result['avg_response_time']:.3f}s")
        print(f"  Success rate: {result['success_rate']:.1%}")

        if result["last_response"] and isinstance(result["last_response"], dict):
            if "symbols" in result["last_response"]:
                print(f"  Symbols found: {len(result['last_response']['symbols'])}")
            elif "status" in result["last_response"]:
                print(f"  Server status: {result['last_response']['status']}")

    # Generate summary
    print(f"\n" + "=" * 50)
    print("TEST SUMMARY")
    print("=" * 50)

    successful_tests = sum(1 for r in results if r["success_rate"] >= 0.8)
    total_tests = len(results)
    avg_response_time = statistics.mean([r["avg_response_time"] for r in results])

    print(f"Tests passed: {successful_tests}/{total_tests} ({successful_tests/total_tests:.1%})")
    print(f"Average response time: {avg_response_time:.3f}s")
    print(f"Fastest response: {min(r['min_response_time'] for r in results):.3f}s")
    print(f"Slowest response: {max(r['max_response_time'] for r in results):.3f}s")

    # Performance analysis
    fast_responses = sum(1 for r in results if r["avg_response_time"] < 0.1)
    print(f"Sub-100ms responses: {fast_responses}/{total_tests} ({fast_responses/total_tests:.1%})")

    # Overall assessment
    overall_success = successful_tests >= total_tests * 0.9 and avg_response_time < 1.0

    print(f"\nOVERALL ASSESSMENT: {'PASS' if overall_success else 'FAIL'}")

    if overall_success:
        print("[OK] Rust API is performing excellently!")
        print("   - High success rates")
        print("   - Fast response times")
        print("   - All endpoints functional")
    else:
        print("[ERROR] Some issues detected:")
        failed_tests = [r for r in results if r["success_rate"] < 0.8]
        for test in failed_tests:
            print(f"   - {test['url']}: {test['success_rate']:.1%} success rate")

    # Save detailed results
    with open("api_test_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nDetailed results saved to api_test_results.json")

    return overall_success

def test_concurrent_performance():
    """Test concurrent request handling"""
    print(f"\n" + "=" * 50)
    print("CONCURRENT PERFORMANCE TEST")
    print("=" * 50)

    import concurrent.futures

    def make_request():
        start = time.time()
        try:
            response = requests.get("http://127.0.0.1:8001/symbols?q=parse&limit=5", timeout=5)
            return time.time() - start, response.status_code == 200
        except:
            return time.time() - start, False

    # Test with 20 concurrent requests
    print("Testing 20 concurrent requests...")

    start_time = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(make_request) for _ in range(20)]
        request_results = [f.result() for f in concurrent.futures.as_completed(futures)]

    total_time = time.time() - start_time
    times, successes = zip(*request_results)

    avg_response_time = statistics.mean(times)
    success_rate = sum(successes) / len(successes)
    requests_per_second = len(request_results) / total_time

    print(f"Results:")
    print(f"  Total time: {total_time:.3f}s")
    print(f"  Avg response: {avg_response_time:.3f}s")
    print(f"  Success rate: {success_rate:.1%}")
    print(f"  Throughput: {requests_per_second:.1f} requests/sec")

    concurrent_success = success_rate >= 0.9 and requests_per_second >= 10
    print(f"  Assessment: {'PASS' if concurrent_success else 'FAIL'}")

    return concurrent_success

if __name__ == "__main__":
    import sys

    print("RepoGraph Rust API Testing Suite")

    # Test basic API functionality
    api_success = run_api_compatibility_tests()

    # Test concurrent performance
    concurrent_success = test_concurrent_performance()

    overall_success = api_success and concurrent_success

    print(f"\nFINAL RESULT: {'SUCCESS' if overall_success else 'FAILURE'}")

    sys.exit(0 if overall_success else 1)