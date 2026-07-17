from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any

try:
    from confluent_kafka import Consumer, KafkaError
except Exception as exc:  # pragma: no cover - optional dependency
    raise SystemExit(f"confluent-kafka is required: {exc}") from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trace decision events across core Kafka topics.")
    parser.add_argument("--decision-id", required=True, help="Decision ID to trace.")
    parser.add_argument(
        "--bootstrap-servers",
        default=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092"),
        help="Kafka bootstrap servers.",
    )
    parser.add_argument(
        "--topics",
        default="decision.lifecycle,eval.results,decision.lifecycle.dlq",
        help="Comma-separated topics to consume.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=20.0, help="Stop after this timeout.")
    parser.add_argument("--group-id", default=f"trace-decision-{int(time.time())}", help="Kafka consumer group.")
    return parser


def _to_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def main() -> int:
    args = _build_parser().parse_args()
    decision_id = str(args.decision_id).strip()
    topics = [topic.strip() for topic in str(args.topics).split(",") if topic.strip()]
    consumer = Consumer(
        {
            "bootstrap.servers": str(args.bootstrap_servers),
            "group.id": str(args.group_id),
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe(topics)

    deadline = time.monotonic() + float(args.timeout_seconds)
    try:
        while time.monotonic() < deadline:
            msg = consumer.poll(0.5)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                print(json.dumps({"type": "kafka_error", "error": str(msg.error())}, ensure_ascii=True))
                continue
            raw = msg.value()
            if raw is None:
                consumer.commit(message=msg, asynchronous=False)
                continue
            try:
                payload = json.loads(_to_text(raw))
            except Exception:
                consumer.commit(message=msg, asynchronous=False)
                continue
            if not isinstance(payload, dict):
                consumer.commit(message=msg, asynchronous=False)
                continue
            if str(payload.get("decision_id") or "").strip() != decision_id:
                consumer.commit(message=msg, asynchronous=False)
                continue

            key = _to_text(msg.key())
            print(
                json.dumps(
                    {
                        "topic": str(msg.topic() or ""),
                        "partition": int(msg.partition()),
                        "offset": int(msg.offset()),
                        "key": key,
                        "decision_id": decision_id,
                        "status": str(payload.get("status") or ""),
                        "event_type": str(payload.get("event_type") or ""),
                        "payload": payload,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )
            consumer.commit(message=msg, asynchronous=False)
    finally:
        consumer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
