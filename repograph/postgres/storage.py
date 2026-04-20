"""StorageServices — backend-agnostic container for alle storage-lag."""
from __future__ import annotations

from dataclasses import dataclass, field

from repograph.graph.factory import GraphStore
from repograph.postgres.repositories.task_memory import TaskMemoryRepository
from repograph.postgres.repositories.usage_logs import UsageRepository
from repograph.postgres.repositories.verifier_runs import VerifierRunRepository


@dataclass
class StorageServices:
    graph: GraphStore
    task_memory: TaskMemoryRepository = field(default_factory=TaskMemoryRepository)
    verifier_runs: VerifierRunRepository = field(default_factory=VerifierRunRepository)
    usage: UsageRepository = field(default_factory=UsageRepository)
