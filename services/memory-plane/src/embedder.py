from __future__ import annotations

import json
import logging
import os
from typing import List
from urllib.error import URLError
from urllib.request import Request, urlopen

_log = logging.getLogger(__name__)
_DEFAULT_TIMEOUT = 30.0


class Embedder:
    """HTTP client for nomic-embed-text via llama.cpp /embedding endpoint.

    POST {base_url}/embedding  body: {"content": "..."}
    Returns {"embedding": [...float...]}

    Graceful degradation: any network or parse error returns [].
    Uses urllib.request — no requests dependency.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = str(
            base_url or os.environ.get("MODEL_RUNNER_URL", "http://host.docker.internal:8081")
        ).rstrip("/")

    def embed(self, text: str) -> List[float]:
        """Embed a single text string. Returns [] on any error."""
        clean = str(text or "").strip()
        if not clean:
            return []
        body = json.dumps({"content": clean}, ensure_ascii=True).encode("utf-8")
        req = Request(
            url=f"{self.base_url}/embedding",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                payload = json.loads(raw)
                vec = payload.get("embedding") or payload.get("embeddings")
                if not isinstance(vec, list):
                    _log.warning("Embedder: unexpected response format — no 'embedding' key")
                    return []
                return [float(v) for v in vec]
        except Exception as exc:
            _log.warning("Embedder.embed failed: %s", exc)
            return []

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Embed multiple texts sequentially (llama.cpp is not parallel-safe)."""
        return [self.embed(t) for t in texts]
