from __future__ import annotations

import logging
import os

from bus.kafka_events import KafkaEventBus
from babyai_shared.consumers.telemetry_consumer import TelemetryConsumer


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    config_path = os.getenv("KAFKA_CONFIG_PATH", "config/kafka_config.yaml")
    environment = os.getenv("ENVIRONMENT", "development")
    output_path = os.getenv("TELEMETRY_OUTPUT_PATH", "logs/failures.jsonl")

    event_bus = KafkaEventBus(config_path=config_path, environment=environment)
    consumer = TelemetryConsumer(event_bus=event_bus, output_path=output_path)
    consumer.start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
