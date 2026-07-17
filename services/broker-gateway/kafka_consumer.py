"""Migration shim — authoritative code at services/orchestrator-worker/src/kafka_consumer.py."""
import sys as _sys
import os as _os

_src = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "services", "orchestrator-worker", "src",
)
if _src not in _sys.path:
    _sys.path.insert(0, _src)

from kafka_consumer import *  # noqa: F401, F403, E402
from kafka_consumer import KafkaConsumerMixin  # noqa: F401, E402
