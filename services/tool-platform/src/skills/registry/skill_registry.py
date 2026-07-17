"""
skill_runtime/registry/skill_registry.py — SKILL.md-baseret registry.

Læser SKILL.md via SkillLoader, validerer, indexerer på skill_id + domains.
Thread-safe. Genbrug: samme interface-mønster som babyai/skills/registry.py.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional

from skill_runtime.loader.skill_loader import SkillManifest, discover_skill_mds
from skill_runtime.validator.skill_validator import validate_all

_log = logging.getLogger(__name__)

_DEFAULT_ROOTS = [
    Path("skills"),
    Path("/mnt/skills"),
    Path("docs/skills"),
]


class SkillRuntimeRegistry:
    """
    Registry der loader og indekserer alle SKILL.md-skills.

    Genbrug: erstat SkillRegistry fra babyai/skills/registry.py gradvist
    via migration_shim.py.
    """

    def __init__(self, roots: Optional[List[Path]] = None) -> None:
        self._roots  = roots or _DEFAULT_ROOTS
        self._lock   = threading.RLock()
        self._by_id: Dict[str, SkillManifest]         = {}
        self._by_domain: Dict[str, List[SkillManifest]] = {}

    def bootstrap(self) -> int:
        manifests = discover_skill_mds(self._roots)
        valid     = validate_all(manifests)
        with self._lock:
            self._by_id.clear()
            self._by_domain.clear()
            for m in valid:
                self._by_id[m.skill_id] = m
                for domain in m.domains:
                    self._by_domain.setdefault(domain, []).append(m)
        _log.info("skill_runtime_registry_bootstrapped count=%d", len(valid))
        return len(valid)

    def get(self, skill_id: str) -> Optional[SkillManifest]:
        with self._lock:
            return self._by_id.get(skill_id)

    def list_all(self) -> List[SkillManifest]:
        with self._lock:
            return list(self._by_id.values())

    def list_by_domain(self, domain: str) -> List[SkillManifest]:
        with self._lock:
            return list(self._by_domain.get(domain, []))

    def search(self, query: str) -> List[SkillManifest]:
        q = query.lower()
        with self._lock:
            return [
                m for m in self._by_id.values()
                if q in m.skill_id or q in m.description.lower()
                or any(q in t.lower() for t in m.raw_frontmatter.get("triggers", []))
            ]

    def register(self, manifest: SkillManifest) -> None:
        with self._lock:
            self._by_id[manifest.skill_id] = manifest
            for domain in manifest.domains:
                lst = self._by_domain.setdefault(domain, [])
                if manifest not in lst:
                    lst.append(manifest)

    def count(self) -> int:
        with self._lock:
            return len(self._by_id)
