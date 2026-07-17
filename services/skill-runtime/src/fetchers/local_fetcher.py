from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Dict, List

import yaml

from babyai.skills.registry import SkillRecord, SkillSource


def _repo_skills_dir() -> Path:
    """Return <repo_root>/skills/, resolved relative to this file."""
    return Path(__file__).resolve().parents[3] / "skills"


class LocalSkillFetcher:
    BASE_PATHS = [Path("/mnt/skills"), Path("docs/skills"), _repo_skills_dir()]

    async def fetch(self, uri: str) -> str:
        for base in self.BASE_PATHS:
            path = base / uri
            if path.exists() and path.is_file():
                return path.read_text(encoding="utf-8")
        return ""

    def discover(self, registry: Any) -> int:
        """Crawl all BASE_PATHS and register markdown files as skills."""
        count = 0
        for base in self.BASE_PATHS:
            if not base.exists():
                continue
            for path in base.rglob("*.md"):
                content = path.read_text(encoding="utf-8")
                meta = self._parse_frontmatter(content)
                clean = self._strip_frontmatter(content)
                record = SkillRecord(
                    skill_id=self._skill_id(meta, path),
                    source=SkillSource.LOCAL,
                    uri=str(path.relative_to(base)).replace("\\", "/"),
                    domains=self._domains(meta),
                    dimensions=self._dimensions(meta),
                    content=clean,
                    fetched_at=datetime.utcnow(),
                    token_count=self._estimate_tokens(clean),
                )
                self._register_sync(registry, record)
                count += 1
        return count

    def _parse_frontmatter(self, content: str) -> Dict[str, Any]:
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not match:
            return {}
        try:
            parsed = yaml.safe_load(match.group(1))
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _strip_frontmatter(self, content: str) -> str:
        return re.sub(
            r"^---\s*\n.*?\n---\s*\n",
            "",
            content,
            count=1,
            flags=re.DOTALL,
        ).strip()

    def _estimate_tokens(self, text: str) -> int:
        return len(text) // 4

    def _register_sync(self, registry: Any, record: SkillRecord) -> None:
        coroutine = registry.register(record)
        if not hasattr(coroutine, "__await__"):
            return
        try:
            asyncio.get_running_loop()
            raise RuntimeError("LocalSkillFetcher.discover cannot run with an active event loop")
        except RuntimeError as exc:
            if "cannot run" in str(exc):
                raise
        asyncio.run(coroutine)

    def _skill_id(self, meta: Dict[str, Any], path: Path) -> str:
        raw = meta.get("skill_id")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return path.stem

    def _domains(self, meta: Dict[str, Any]) -> List[str]:
        raw = meta.get("domains")
        if isinstance(raw, list):
            values = [str(item).strip() for item in raw if str(item).strip()]
            if values:
                return values
        return ["general"]

    def _dimensions(self, meta: Dict[str, Any]) -> List[str]:
        raw = meta.get("dimensions")
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

