"""Domain logic for Obsidian integration."""

import logging
from typing import Any

from .client import ObsidianClient
from .models import ObsidianNoteSummary, ObsidianContextResult
from .exceptions import ObsidianTimeoutError, ObsidianConnectorError, ObsidianUnauthorizedError

LOGGER = logging.getLogger(__name__)

class ObsidianService:
    def __init__(self):
        self.client = ObsidianClient()

    def search_notes_by_query(self, query: str) -> ObsidianContextResult:
        """Search notes by free text."""
        if not self.client.configured:
            return ObsidianContextResult(notes=[], status="unconfigured")

        try:
            results = self.client.search_simple(query)
            return ObsidianContextResult(
                notes=[self._parse_note(r) for r in results],
                status="ok"
            )
        except ObsidianTimeoutError:
            LOGGER.warning("Obsidian search timed out.")
            return ObsidianContextResult(notes=[], status="timeout")
        except ObsidianUnauthorizedError:
            LOGGER.warning("Obsidian authorization failed.")
            return ObsidianContextResult(notes=[], status="unauthorized")
        except ObsidianConnectorError as exc:
            LOGGER.error(f"Obsidian connector error: {exc}")
            return ObsidianContextResult(notes=[], status="error")

    def search_notes_by_symbol(self, symbol: str) -> ObsidianContextResult:
        """Search notes that refer to a specific software symbol in frontmatter."""
        if not self.client.configured:
            return ObsidianContextResult(notes=[], status="unconfigured")
            
        try:
            # First, structured query via jsonlogic
            # This requires Local REST API to execute the query against its dataview/frontmatter index.
            # E.g. {"in": ["my.symbol", {"var": "frontmatter.symbols"}]}
            # We'll build a simpler jsonlogic payload:
            payload = {
                "some": [
                    {"var": "frontmatter.symbols"},
                    {"==": [{"var": ""}, symbol]}
                ]
            }
            results = self.client.search(payload)
            notes = [self._parse_note(r) for r in results]
            
            if not notes:
                # Fallback to simple full text search if structured fails
                # Often frontmatter arrays might not be indexed perfectly or user typed simply "symbol"
                fallback_results = self.client.search_simple(symbol)
                notes = [self._parse_note(r) for r in fallback_results]
                
            return ObsidianContextResult(notes=notes, status="ok")
        except ObsidianTimeoutError:
            return ObsidianContextResult(notes=[], status="timeout")
        except ObsidianUnauthorizedError:
            return ObsidianContextResult(notes=[], status="unauthorized")
        except ObsidianConnectorError:
            return ObsidianContextResult(notes=[], status="error")

    def _parse_note(self, raw: dict[str, Any]) -> ObsidianNoteSummary:
        frontmatter = raw.get("frontmatter", {}) or {}
        # Parse tags gracefully whether they're a string or a list
        raw_tags = frontmatter.get("tags", [])
        tags = raw_tags if isinstance(raw_tags, list) else [raw_tags] if isinstance(raw_tags, str) else []
        
        return ObsidianNoteSummary(
            filename=raw.get("filename", "unknown"),
            path=raw.get("path", ""),
            content=raw.get("content", ""),
            frontmatter=frontmatter,
            tags=tags
        )
