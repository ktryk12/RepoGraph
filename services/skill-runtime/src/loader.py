"""
babyai/skills/loader.py — SkillBootstrapper

Connects LocalSkillFetcher → SkillRegistry at startup.
Discovers all SKILL.md files under:
  1. <repo_root>/skills/          (canonical — SKILL.md format)
  2. /mnt/skills/                 (Docker volume mount)
  3. docs/skills/                 (legacy path)
  4. Any extra paths passed by the caller.

Usage (from OrchestratorWorker or any FastAPI lifespan):

    from babyai.skills.loader import SkillBootstrapper
    bootstrapper = SkillBootstrapper(registry)
    count = bootstrapper.bootstrap()
    # count = number of skills registered
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from babyai.skills.fetchers.local_fetcher import LocalSkillFetcher
from babyai.skills.registry import SkillRegistry

_log = logging.getLogger(__name__)


class SkillBootstrapper:
    """
    Populate a SkillRegistry from all discoverable SKILL.md files.

    Parameters
    ----------
    registry:     The SkillRegistry to populate.
    extra_paths:  Additional directories to scan beyond the defaults in
                  LocalSkillFetcher.BASE_PATHS.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        extra_paths: List[Path] | None = None,
    ) -> None:
        self._registry = registry
        self._extra_paths = extra_paths or []

    def bootstrap(self) -> int:
        """
        Discover and register all skills. Returns the total count registered.
        Logs a warning (never raises) if a path is missing or unreadable.
        """
        fetcher = LocalSkillFetcher()

        # Extend with any caller-supplied paths.
        if self._extra_paths:
            fetcher.BASE_PATHS = list(fetcher.BASE_PATHS) + self._extra_paths

        total = 0
        for base in fetcher.BASE_PATHS:
            if not base.exists():
                continue
            try:
                # discover() scans a single base path — call per-path so we
                # can log individual results.
                single = LocalSkillFetcher()
                single.BASE_PATHS = [base]
                count = single.discover(self._registry)
                if count:
                    _log.info(
                        "skill_bootstrap path=%s discovered=%d", base, count
                    )
                total += count
            except Exception as exc:
                _log.warning(
                    "skill_bootstrap_error path=%s error=%s", base, exc
                )

        _log.info("skill_bootstrap_complete total=%d", total)
        return total
