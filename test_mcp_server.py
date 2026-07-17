#!/usr/bin/env python3
"""
Test script for RepoGraph Rust MCP Server
Tests MCP protocol communication via stdio
"""

import json
import subprocess
import sys
import time
from typing import Dict, Any

def send_mcp_request(process: subprocess.Popen, request: Dict[str, Any]) -> Dict[str, Any]:
    """Send MCP request and get response"""
    request_json = json.dumps(request)
    print(f"→ Sending: {request_json}")

    process.stdin.write(request_json + "\n")
    process.stdin.flush()

    response_line = process.stdout.readline()
    if not response_line:
        raise Exception("No response from MCP server")

    print(f"← Received: {response_line.strip()}")
    return json.loads(response_line.strip())

def test_mcp_server():
    """Test MCP server functionality"""
    print("Testing RepoGraph Rust MCP Server")
    print("=" * 50)

    # Start MCP server process
    cmd = ["repograph-poc\\target\\release\\repograph-mcp.exe", "--verbose"]
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    try:
        # Wait a moment for server to start
        time.sleep(0.5)

        # Test 1: Initialize
        print("\n[TEST 1] Initialize")
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {}
            }
        }

        response = send_mcp_request(process, init_request)
        assert response["jsonrpc"] == "2.0"
        assert "result" in response
        print("[OK] Initialize successful")

        # Test 2: List tools
        print("\n📋 Test 2: List Tools")
        tools_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list"
        }

        response = send_mcp_request(process, tools_request)
        tools = response["result"]["tools"]
        print(f"✅ Found {len(tools)} tools:")
        for tool in tools:
            print(f"   • {tool['name']}: {tool['description']}")

        # Test 3: Repository Status
        print("\n📋 Test 3: Repository Status")
        status_request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "repo_status",
                "arguments": {}
            }
        }

        response = send_mcp_request(process, status_request)
        if "result" in response and response["result"]["content"]:
            content = response["result"]["content"][0]["text"]
            print("✅ Repository status retrieved:")
            print("   " + content.replace("\n", "\n   "))

        # Test 4: Search symbols
        print("\n📋 Test 4: Search Symbols")
        search_request = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "search_symbols",
                "arguments": {
                    "query": "main",
                    "limit": 5
                }
            }
        }

        response = send_mcp_request(process, search_request)
        if "result" in response and response["result"]["content"]:
            content = response["result"]["content"][0]["text"]
            print("✅ Symbol search completed:")
            print("   " + content.replace("\n", "\n   "))

        print("\n🎯 All MCP tests completed successfully!")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        # Print stderr if available
        stderr_output = process.stderr.read()
        if stderr_output:
            print(f"Server stderr: {stderr_output}")
        return False

    finally:
        # Cleanup
        process.terminate()
        process.wait(timeout=5)

    return True

def test_mcp_with_preloaded_repo():
    """Test MCP server with preloaded repository"""
    print("\n🔌 Testing MCP Server with Preloaded Repository")
    print("=" * 60)

    # Start MCP server with preloaded repo
    cmd = [
        "repograph-poc\\target\\release\\repograph-mcp.exe",
        "--preload-repo", "..",
        "--verbose"
    ]

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    try:
        # Wait for preloading to complete
        time.sleep(3)

        # Initialize
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize"
        }
        response = send_mcp_request(process, init_request)

        # Test search with actual data
        print("\n📋 Testing Search with Preloaded Data")
        search_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "search_symbols",
                "arguments": {
                    "query": "parse",
                    "limit": 3
                }
            }
        }

        response = send_mcp_request(process, search_request)
        if "result" in response:
            content = response["result"]["content"][0]["text"]
            print("✅ Search results:")
            print("   " + content.replace("\n", "\n   "))

        print("\n🎯 Preloaded repository test completed!")

    except Exception as e:
        print(f"\n❌ Preloaded test failed: {e}")
        stderr_output = process.stderr.read()
        if stderr_output:
            print(f"Server stderr: {stderr_output}")
        return False

    finally:
        process.terminate()
        process.wait(timeout=5)

    return True

if __name__ == "__main__":
    print("🦀 RepoGraph Rust MCP Server Tests")

    # Test basic MCP functionality
    success1 = test_mcp_server()

    # Test with preloaded repo
    success2 = test_mcp_with_preloaded_repo()

    if success1 and success2:
        print("\n✅ All MCP tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some MCP tests failed!")
        sys.exit(1)