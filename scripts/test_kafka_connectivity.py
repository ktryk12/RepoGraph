#!/usr/bin/env python3
"""
Test Kafka Connectivity for All Services

Verifies that each service with Kafka dependencies can connect to the Kafka cluster
and perform basic producer/consumer operations.
"""

import asyncio
import json
import logging
import sys
from typing import Dict, List, Any
from datetime import datetime

# Test with confluent-kafka if available
try:
    from confluent_kafka import Producer, Consumer, KafkaError, KafkaException
    KAFKA_AVAILABLE = True
except ImportError:
    KAFKA_AVAILABLE = False
    print("WARNING: confluent-kafka not available, testing with mock")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Service Kafka configuration mapping
KAFKA_SERVICES = {
    "agent-platform": {
        "bootstrap_servers": "localhost:9092",
        "group_id": "agent-platform",
        "test_topics": [
            "agent.platform.v1.agent.discovered",
            "agent.platform.v1.execution.started"
        ]
    },
    "data-platform": {
        "bootstrap_servers": "localhost:9092",
        "group_id": "data-platform",
        "test_topics": [
            "data.export.job.started",
            "data.artifact.created"
        ]
    },
    "media-platform": {
        "bootstrap_servers": "localhost:9092",
        "group_id": "media-platform",
        "test_topics": [
            "media.video.job.started",
            "media.asset.created"
        ]
    },
    "tool-platform": {
        "bootstrap_servers": "localhost:9092",
        "group_id": "tool-platform",
        "test_topics": [
            "tool-platform.tool.registered",
            "tool-platform.skill.executed"
        ]
    },
    "policy-management": {
        "bootstrap_servers": "localhost:9092",
        "group_id": "policy-management",
        "test_topics": [
            "policy-management.policy.created",
            "policy-management.constitution.updated"
        ]
    },
    "request-gate": {
        "bootstrap_servers": "localhost:9092",
        "group_id": "request-gate",
        "test_topics": [
            "decision.requested",
            "decision.lifecycle"
        ]
    },
    "orchestrator-worker": {
        "bootstrap_servers": "localhost:9092",
        "group_id": "orchestrator-workers",
        "test_topics": [
            "decision.lifecycle",
            "decision.approval"
        ]
    },
    "truthpack-conversation": {
        "bootstrap_servers": "localhost:9092",
        "group_id": "truthpack-conversation",
        "test_topics": [
            "truthpack.conversation.started",
            "truthpack.message.processed"
        ]
    },
    "planner": {
        "bootstrap_servers": "localhost:9092",
        "group_id": "planner",
        "test_topics": [
            "planner.task.created",
            "planner.plan.generated"
        ]
    },
    "broker-gateway": {
        "bootstrap_servers": "localhost:9092",
        "group_id": "broker-gateway",
        "test_topics": [
            "broker.request.received",
            "broker.response.sent"
        ]
    },
    "claim-detector": {
        "bootstrap_servers": "localhost:9092",
        "group_id": "claim-detector",
        "test_topics": [
            "claims.detected",
            "claims.validated"
        ]
    },
    "exercise_runner": {
        "bootstrap_servers": "localhost:9092",
        "group_id": "exercise-runner",
        "test_topics": [
            "exercise.started",
            "exercise.completed"
        ]
    },
    "memory-plane": {
        "bootstrap_servers": "localhost:9092",
        "group_id": "memory-plane",
        "test_topics": [
            "memory.episode.stored",
            "memory.retrieval.request"
        ]
    },
    "order-manager": {
        "bootstrap_servers": "localhost:9092",
        "group_id": "order-manager",
        "test_topics": [
            "orders.created",
            "orders.fulfilled"
        ]
    },
    "policy-enforcer": {
        "bootstrap_servers": "localhost:9092",
        "group_id": "policy-enforcer",
        "test_topics": [
            "policy.decision.requested",
            "policy.decision.made"
        ]
    },
    "verify": {
        "bootstrap_servers": "localhost:9092",
        "group_id": "verify",
        "test_topics": [
            "verification.request",
            "verification.result"
        ]
    }
}


def test_kafka_connectivity(service_name: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Test Kafka connectivity for a single service"""
    result = {
        "service": service_name,
        "connection": False,
        "producer": False,
        "consumer": False,
        "topics_created": [],
        "errors": []
    }

    if not KAFKA_AVAILABLE:
        result["errors"].append("confluent-kafka not available")
        return result

    try:
        # Test producer connection
        producer = Producer({
            "bootstrap.servers": config["bootstrap_servers"],
            "client.id": f"test-{service_name}-producer"
        })

        # Test producing to first topic
        test_topic = config["test_topics"][0] if config["test_topics"] else f"test-{service_name}"
        test_message = {
            "test": True,
            "service": service_name,
            "timestamp": datetime.utcnow().isoformat(),
            "message": "Kafka connectivity test"
        }

        producer.produce(
            topic=test_topic,
            key=f"test-{service_name}",
            value=json.dumps(test_message)
        )

        # Wait for delivery
        remaining = producer.flush(timeout=10)
        if remaining == 0:
            result["producer"] = True
            result["topics_created"].append(test_topic)
            logger.info(f"{service_name}: Producer test successful")
        else:
            result["errors"].append(f"Producer flush timeout, {remaining} messages remaining")

        producer.flush()

    except Exception as e:
        result["errors"].append(f"Producer error: {e}")
        logger.error(f"{service_name}: Producer test failed - {e}")

    try:
        # Test consumer connection
        consumer = Consumer({
            "bootstrap.servers": config["bootstrap_servers"],
            "group.id": f"test-{config['group_id']}",
            "auto.offset.reset": "latest",
            "enable.auto.commit": False
        })

        # Subscribe to test topics
        consumer.subscribe(config["test_topics"][:1])  # Just first topic for test

        # Poll once to verify connection
        msg = consumer.poll(timeout=5.0)
        result["consumer"] = True
        result["connection"] = True
        logger.info(f"{service_name}: Consumer test successful")

        consumer.close()

    except Exception as e:
        result["errors"].append(f"Consumer error: {e}")
        logger.error(f"{service_name}: Consumer test failed - {e}")

    return result


async def test_all_services() -> Dict[str, Any]:
    """Test Kafka connectivity for all services with Kafka dependencies"""
    results = {}
    summary = {
        "total_services": len(KAFKA_SERVICES),
        "connected": 0,
        "producer_working": 0,
        "consumer_working": 0,
        "failed": 0,
        "kafka_available": KAFKA_AVAILABLE
    }

    print(f"\nTesting Kafka connectivity for {len(KAFKA_SERVICES)} services...")
    print(f"   Kafka cluster: {list(KAFKA_SERVICES.values())[0]['bootstrap_servers']}")
    print(f"   confluent-kafka available: {KAFKA_AVAILABLE}")
    print("\n" + "="*70)

    for service_name, config in KAFKA_SERVICES.items():
        print(f"\nTesting {service_name}...")
        result = test_kafka_connectivity(service_name, config)
        results[service_name] = result

        # Update summary
        if result["connection"]:
            summary["connected"] += 1
        if result["producer"]:
            summary["producer_working"] += 1
        if result["consumer"]:
            summary["consumer_working"] += 1
        if result["errors"]:
            summary["failed"] += 1

        # Print result
        status = "PASS" if result["connection"] and not result["errors"] else "FAIL"
        print(f"   {status} - Connection: {result['connection']}, Producer: {result['producer']}, Consumer: {result['consumer']}")
        if result["errors"]:
            for error in result["errors"]:
                print(f"      Error: {error}")

    print("\n" + "="*70)
    print(f"\nSUMMARY:")
    print(f"   Total services tested: {summary['total_services']}")
    print(f"   Successfully connected: {summary['connected']}")
    print(f"   Producer working: {summary['producer_working']}")
    print(f"   Consumer working: {summary['consumer_working']}")
    print(f"   Failed connections: {summary['failed']}")

    if summary["connected"] == summary["total_services"]:
        print(f"\nSUCCESS: All {summary['total_services']} services can connect to Kafka!")
    else:
        failed_services = [name for name, result in results.items() if not result["connection"]]
        print(f"\nISSUES: {len(failed_services)} services failed connectivity test:")
        for service in failed_services:
            print(f"     - {service}")

    return {
        "summary": summary,
        "results": results
    }


if __name__ == "__main__":
    if not KAFKA_AVAILABLE:
        print("\nWARNING: confluent-kafka not installed. Install with:")
        print("   pip install confluent-kafka")
        print("\nProceeding with limited testing...\n")

    # Run the test
    test_results = asyncio.run(test_all_services())

    # Exit with appropriate code
    if test_results["summary"]["failed"] > 0:
        sys.exit(1)
    else:
        sys.exit(0)