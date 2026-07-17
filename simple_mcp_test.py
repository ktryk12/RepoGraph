#!/usr/bin/env python3
"""
Simple test script for RepoGraph Rust MCP Server
Tests basic MCP functionality without Unicode characters
"""

import json
import subprocess
import sys
import time

def test_mcp_basic():
    """Test basic MCP server functionality"""
    print("Testing RepoGraph Rust MCP Server")
    print("=" * 40)

    # Start MCP server
    cmd = ["repograph-poc\\target\\release\\repograph-mcp.exe"]
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    try:
        time.sleep(0.5)

        # Test initialize
        print("\nTesting initialize...")
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {}
        }

        request_json = json.dumps(init_request)
        process.stdin.write(request_json + "\n")
        process.stdin.flush()

        response_line = process.stdout.readline()
        response = json.loads(response_line.strip())

        assert response["jsonrpc"] == "2.0"
        assert "result" in response
        print("[OK] Initialize successful")

        # Test list tools
        print("\nTesting list tools...")
        tools_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list"
        }

        request_json = json.dumps(tools_request)
        process.stdin.write(request_json + "\n")
        process.stdin.flush()

        response_line = process.stdout.readline()
        response = json.loads(response_line.strip())

        tools = response["result"]["tools"]
        print(f"[OK] Found {len(tools)} tools")
        for tool in tools:
            print(f"  - {tool['name']}")

        # Test repo status
        print("\nTesting repo status...")
        status_request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "repo_status",
                "arguments": {}
            }
        }

        request_json = json.dumps(status_request)
        process.stdin.write(request_json + "\n")
        process.stdin.flush()

        response_line = process.stdout.readline()
        response = json.loads(response_line.strip())

        if "result" in response:
            print("[OK] Repository status retrieved")

        print("\n[SUCCESS] All MCP tests completed!")
        return True

    except Exception as e:
        print(f"\n[ERROR] Test failed: {e}")
        stderr_output = process.stderr.read()
        if stderr_output:
            print(f"Server stderr: {stderr_output}")
        return False

    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()

if __name__ == "__main__":
    success = test_mcp_basic()
    sys.exit(0 if success else 1)