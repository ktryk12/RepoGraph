"""
skill_runtime/registry/migration_shim.py — Bagud-kompatibel wrapper.

Giver SkillRuntimeRegistry samme interface som babyai/skills/registry.SkillRegistry
så eksisterende kode ikke behøver ændres under migrationen.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from babyai.skills.registry import SkillRecord, SkillSource
from skill_runtime.registry.skill_registry import SkillRuntimeRegistry


class MigratedSkillRegistry:
    """
    Drop-in erstatning for SkillRegistry.
    Delegerer til SkillRuntimeRegistry internt men eksponerer SkillRecord-interface.
    """

    def __init__(self, roots: Optional[List[Path]] = None) -> None:
        self._runtime = SkillRuntimeRegistry(roots)

    def bootstrap(self) -> int:
        return self._runtime.bootstrap()

    def get_skill(self, skill_id: str) -> Optional[SkillRecord]:
        m = self._runtime.get(skill_id)
        if m is None:
            return None
        return self._manifest_to_record(m)

    def list_skills(self, domain: Optional[str] = None) -> List[SkillRecord]:
        if domain:
            manifests = self._runtime.list_by_domain(domain)
        else:
            manifests = self._runtime.list_all()
        return [self._manifest_to_record(m) for m in manifests]

    def search(self, query: str) -> List[SkillRecord]:
        return [self._manifest_to_record(m) for m in self._runtime.search(query)]

    def count(self) -> int:
        return self._runtime.count()

    @staticmethod
    def _manifest_to_record(m) -> SkillRecord:
        return SkillRecord(
            skill_id=m.skill_id,
            source=SkillSource.LOCAL,
            uri=str(m.source_path),
            domains=m.domains,
            dimensions=m.dimensions,
            content=m.body,
            fetched_at=datetime.utcnow(),
            ttl_seconds=86400,
        )
