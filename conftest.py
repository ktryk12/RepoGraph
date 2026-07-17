"""Root conftest — test environment bootstrap only."""
import os
import sys
from pathlib import Path

# services/aesa/aesa/ is the canonical aesa package source (microservices layout).
# Insert its parent so `import aesa` resolves to services/aesa/aesa/ rather than
# the deleted root-level aesa/ copy.
_aesa_src = str(Path(__file__).parent / "services" / "aesa" / "aesa")
_aesa_parent = str(Path(__file__).parent / "services" / "aesa")
if _aesa_parent not in sys.path:
    sys.path.insert(0, _aesa_parent)

# Enable AUTO_APPROVE e2e test (requires Kafka running on localhost:29092)
os.environ.setdefault("AUTO_APPROVE", "true")

# Exclude tests that require optional dependencies not installed in dev environment
collect_ignore = [
    str(Path(__file__).parent / "tests" / "skills" / "test_skill_crawler.py"),
    # Tests for deleted packages (analysis/, docs/)
    str(Path(__file__).parent / "tests" / "test_curriculum_miner.py"),
    str(Path(__file__).parent / "tests" / "test_curriculum_miner_v2_deterministic.py"),
    str(Path(__file__).parent / "tests" / "test_selection_matrix_doc.py"),
    # Requires analysis.curriculum_miner subprocess (analysis/ deleted)
    str(Path(__file__).parent / "tests" / "test_telemetry_bridge_kafka.py"),
]
