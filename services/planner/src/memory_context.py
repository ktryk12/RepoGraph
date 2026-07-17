from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

_log = logging.getLogger(__name__)

_MAX_CONTEXT_CHARS = 500


class PlannerMemoryContext:
    """Retrieves relevant memories from memory-plane before an episode starts.

    All retrieval is fail-open: HTTP errors, timeouts, and unavailability
    are caught and logged.  Episode start is NEVER blocked by this class.

    Timeout is intentionally short (5s) — planner throughput must not degrade
    when memory-plane is under load or temporarily unavailable.
    """

    def __init__(self, memory_plane_url: Optional[str] = None) -> None:
        self._url = (
            memory_plane_url
            or os.environ.get("MEMORY_PLANE_URL", "http://memory-plane:8101")
        ).rstrip("/")
        self._timeout = 5.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve_for_episode(
        self,
        scenario: str,
        agent_context: Optional[Dict[str, Any]] = None,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """Retrieve relevant memories for an upcoming episode.

        Performs two searches in sequence (scenario context + pattern history)
        and returns a combined result.  Always returns within timeout + network
        latency; never raises.
        """
        started = time.perf_counter()
        try:
            scenario_memories = self._search(
                query=f"swarm scenario {scenario}",
                top_k=top_k,
                min_importance=0.5,
            )
            pattern_memories = self._search(
                query=f"emergence pattern {scenario}",
                top_k=top_k,
                entity_type="pattern",
                min_importance=0.4,
            )
            total = len(scenario_memories) + len(pattern_memories)
            retrieval_ms = round((time.perf_counter() - started) * 1000.0, 2)
            return {
                "scenario_memories": scenario_memories,
                "pattern_memories": pattern_memories,
                "total_retrieved": total,
                "retrieval_ms": retrieval_ms,
            }
        except Exception as exc:
            _log.warning("Memory retrieval failed for scenario=%s: %s", scenario, exc)
            return {
                "scenario_memories": [],
                "pattern_memories": [],
                "total_retrieved": 0,
                "retrieval_ms": 0.0,
            }

    def format_for_context(self, retrieval: Dict[str, Any]) -> str:
        """Format retrieved memories into a text block for episode context.

        Hard cap at _MAX_CONTEXT_CHARS to prevent bloated planner contexts.
        """
        lines: List[str] = ["Relevant history for this episode:"]

        for mem in retrieval.get("scenario_memories", []):
            content = str(mem.get("content", "")).strip()
            importance = mem.get("importance", "?")
            lines.append(f"- {content} (importance: {importance})")

        patterns = retrieval.get("pattern_memories", [])
        if patterns:
            lines.append("Patterns observed previously:")
            for mem in patterns:
                content = str(mem.get("content", "")).strip()
                similarity = mem.get("similarity", "?")
                lines.append(f"- {content} (similarity: {similarity})")

        text = "\n".join(lines)
        if len(text) > _MAX_CONTEXT_CHARS:
            text = text[: _MAX_CONTEXT_CHARS - 3] + "..."
        return text

    async def retrieve_async(
        self,
        scenario: str,
        agent_context: Optional[Dict[str, Any]] = None,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """Async wrapper — run retrieve_for_episode in a thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.retrieve_for_episode(scenario, agent_context, top_k),
        )

    # ------------------------------------------------------------------
    # Internal HTTP helper
    # ------------------------------------------------------------------

    def _search(
        self,
        query: str,
        top_k: int,
        entity_type: Optional[str] = None,
        min_importance: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """POST to /memory/search.  Returns empty list on any error."""
        payload: Dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            "min_importance": min_importance,
        }
        if entity_type:
            payload["entity_type"] = entity_type

        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        req = Request(
            url=f"{self._url}/memory/search",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return list(data.get("results", []))
        except Exception as exc:
            _log.debug("memory search failed query=%r: %s", query, exc)
            return []
