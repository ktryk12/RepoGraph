"""Conftest for request-gate service tests.

Adds services/request-gate/src/ to sys.path so tests can import:
    from application.use_cases import ValidateAndEnqueueDecisionRequest
    from infrastructure.kafka_consumer import KafkaDecisionRequestedConsumer
    import main as request_gate_main
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_src = os.path.join(os.path.dirname(_here), "src")
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(_here)))

for _p in [_src, _repo_root]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("AUTO_APPROVE", "true")
