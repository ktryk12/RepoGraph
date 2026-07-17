"""
skill_runtime/loader/skill_loader.py — SKILL.md loader.

Parser SKILL.md filer med YAML-frontmatter og returnerer SkillManifest.
Genbrug: LocalSkillFetcher til file-discovery, python-frontmatter til parse.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)


@dataclass
class SkillManifest:
    skill_id:      str
    name:          str
    version:       str
    description:   str
    domains:       List[str]
    dimensions:    List[str]
    expert_routing: Dict[str, Any]
    babyai_conventions: Dict[str, Any]
    telemetry:     Dict[str, Any]
    body:          str          # markdown body uden frontmatter
    source_path:   Path
    raw_frontmatter: Dict[str, Any] = field(default_factory=dict)

    @property
    def model(self) -> str:
        return self.expert_routing.get("model", "general")

    @property
    def emit_events(self) -> List[str]:
        return self.telemetry.get("emit_events", [
            f"skill.{self.skill_id}.started",
            f"skill.{self.skill_id}.completed",
        ])


def _parse_frontmatter(text: str):
    """Minimal YAML frontmatter parser — ingen tredjeparts-dep nødvendig."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    yaml_block = text[3:end].strip()
    body       = text[end + 4:].strip()
    fm: Dict[str, Any] = {}
    try:
        # Forsøg med PyYAML hvis tilgængeligt
        import yaml
        fm = yaml.safe_load(yaml_block) or {}
    except ImportError:
        # Fallback: simpel linje-parser
        for line in yaml_block.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                k = k.strip()
                v = v.strip()
                if v.startswith("[") and v.endswith("]"):
                    fm[k] = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
                else:
                    fm[k] = v.strip("\"'")
    return fm, body


def load_skill_md(path: Path) -> Optional[SkillManifest]:
    try:
        text = path.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)
        if not fm:
            _log.warning("skill_loader_no_frontmatter path=%s", path)
            return None

        skill_id = str(fm.get("skill_id") or fm.get("name") or path.parent.name or "").strip()
        if not skill_id:
            _log.warning("skill_loader_missing_id path=%s", path)
            return None

        return SkillManifest(
            skill_id=skill_id,
            name=str(fm.get("name", skill_id)),
            version=str(fm.get("version", "1.0.0")),
            description=str(fm.get("description", "")).strip(),
            domains=list(fm.get("domains") or []),
            dimensions=list(fm.get("dimensions") or []),
            expert_routing=dict(fm.get("expert_routing") or {}),
            babyai_conventions=dict(fm.get("babyai_conventions") or {}),
            telemetry=dict(fm.get("telemetry") or {}),
            body=body,
            source_path=path,
            raw_frontmatter=fm,
        )
    except Exception as exc:
        _log.error("skill_loader_parse_error path=%s error=%s", path, exc)
        return None


def discover_skill_mds(roots: List[Path]) -> List[SkillManifest]:
    manifests = []
    seen_ids: set = set()
    for root in roots:
        if not root.exists():
            continue
        for md_path in sorted(root.rglob("SKILL.md")):
            manifest = load_skill_md(md_path)
            if manifest is None:
                continue
            if manifest.skill_id in seen_ids:
                _log.warning("skill_loader_duplicate_id id=%s path=%s", manifest.skill_id, md_path)
                continue
            seen_ids.add(manifest.skill_id)
            manifests.append(manifest)
    _log.info("skill_loader_discovered count=%d", len(manifests))
    return manifests
