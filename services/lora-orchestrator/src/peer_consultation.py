from __future__ import annotations

import json
import logging
import socket
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from babyai_shared.ports.ssrn_port import ConfidenceDecisionPort
from babyai.lora.registry_loader import LoRARegistry

logger = logging.getLogger(__name__)

# (domain, base_url, prompt) -> (answer, confidence)
PeerCallFn = Callable[[str, str, str], Tuple[str, float]]

_DEFAULT_TIMEOUT_MS = 500
_PEER_CONFIDENCE = 0.70  # fixed confidence assigned to peer answers


@dataclass(frozen=True)
class PeerResult:
    answer: str
    confidence: float
    peers_consulted: list[str] = field(default_factory=list)
    aggregation_method: str = "primary"  # 'vote' | 'median' | 'primary'


class PeerConsultation:
    """Confidence-drevet peer consultation.

    Når primær-eksperten returnerer lav confidence kalder denne klasse
    peer-eksperter parallelt og aggregerer de bedste svar.
    Primær-svaret er *altid* fallback — ingen breaking changes.
    """

    def __init__(
        self,
        *,
        registry: LoRARegistry,
        timeout_ms: int = _DEFAULT_TIMEOUT_MS,
        call_peer_fn: PeerCallFn | None = None,
    ) -> None:
        self._registry = registry
        self._timeout_seconds = max(0.05, timeout_ms / 1000.0)
        self._call_peer_fn = call_peer_fn or _default_call_peer

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_consult(
        self,
        confidence_result: ConfidenceDecisionPort | float,
        *,
        threshold: float = 0.65,
    ) -> bool:
        """Returnerer True hvis confidence er under threshold."""
        if isinstance(confidence_result, ConfidenceDecisionPort):
            return confidence_result.used_fallback or confidence_result.confidence < threshold
        return float(confidence_result) < threshold

    def consult(
        self,
        *,
        prompt: str,
        primary_answer: str,
        primary_domain: str,
        confidence_result: ConfidenceDecisionPort | float,
        timeout_ms: int | None = None,
    ) -> PeerResult:
        """Kald peer-eksperter parallelt og returnér det bedste svar.

        Fejler aldrig: timeout og netværksfejl giver fallback til primary.
        """
        try:
            peer_domains = self._registry.get_peer_domains(primary_domain)
        except KeyError:
            logger.debug("peer_consultation_no_peers domain=%s", primary_domain)
            return self._primary_fallback(primary_answer, confidence_result)

        if not peer_domains:
            return self._primary_fallback(primary_answer, confidence_result)

        timeout_s = (
            max(0.05, timeout_ms / 1000.0)
            if timeout_ms is not None
            else self._timeout_seconds
        )

        peer_responses: list[tuple[str, str, float]] = []
        futures: dict[str, Future[tuple[str, float]]] = {}

        with ThreadPoolExecutor(max_workers=len(peer_domains)) as pool:
            for domain in peer_domains:
                try:
                    port = self._registry.get_port(domain)
                    base_url = f"http://model-runner:{port}"
                except KeyError:
                    continue
                futures[domain] = pool.submit(self._call_peer_fn, domain, base_url, prompt)

            for domain, future in futures.items():
                try:
                    answer, conf = future.result(timeout=timeout_s)
                    peer_responses.append((domain, answer, conf))
                    logger.info(
                        "peer_consultation_ok domain=%s confidence=%.3f", domain, conf
                    )
                except FuturesTimeout:
                    logger.warning(
                        "peer_consultation_timeout domain=%s timeout_s=%.3f",
                        domain,
                        timeout_s,
                    )
                    _try_increment_counter("peer_consultation_timeout_total")
                except Exception as exc:
                    logger.warning(
                        "peer_consultation_error domain=%s error=%s", domain, exc
                    )

        if not peer_responses:
            return self._primary_fallback(primary_answer, confidence_result)

        primary_conf = _conf_float(confidence_result)
        all_responses: list[tuple[str, str, float]] = [
            (primary_domain, primary_answer, primary_conf),
            *peer_responses,
        ]
        _, best_answer, best_conf = max(all_responses, key=lambda t: t[2])
        peers_consulted = [d for d, _, _ in peer_responses]
        method = "vote" if len(peer_responses) >= 2 else "median"

        logger.info(
            "peer_consultation_complete primary_domain=%s peers=%s best_conf=%.3f method=%s",
            primary_domain,
            peers_consulted,
            best_conf,
            method,
        )
        return PeerResult(
            answer=best_answer,
            confidence=best_conf,
            peers_consulted=peers_consulted,
            aggregation_method=method,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _primary_fallback(
        self,
        primary_answer: str,
        confidence_result: ConfidenceDecisionPort | float,
    ) -> PeerResult:
        return PeerResult(
            answer=primary_answer,
            confidence=_conf_float(confidence_result),
            peers_consulted=[],
            aggregation_method="primary",
        )


# ------------------------------------------------------------------
# Default peer call (urllib — same pattern as LlamaCppRunnerGateway)
# ------------------------------------------------------------------

def _default_call_peer(domain: str, base_url: str, prompt: str) -> tuple[str, float]:
    """Call a peer llama.cpp endpoint. Returns (answer, confidence)."""
    base = str(base_url).rstrip("/")
    payload = {
        "prompt": str(prompt).strip(),
        "seed": 42,
        "n_predict": 256,
        "temperature": 0.2,
        "stream": False,
    }
    body = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    req = Request(
        url=f"{base}/completion",
        data=body,
        headers={"content-type": "application/json", "accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=5.0) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()
            data = json.loads(raw) if raw else {}
    except HTTPError as exc:
        raise RuntimeError(f"peer_http_error domain={domain} status={exc.code}") from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise RuntimeError(f"peer_unreachable domain={domain}: {exc}") from exc

    text = _extract_text(data)
    return text, _PEER_CONFIDENCE


# ------------------------------------------------------------------
# Integration helper: apply PeerResult onto an ExpertResult
# ------------------------------------------------------------------

def apply_peer_result_to_expert(
    result: Any,  # aesa.core.types.ExpertResult (frozen dataclass)
    peer: PeerResult,
) -> Any:
    """Return a new ExpertResult with peer answer metadata merged in.

    The peer answer is stored in output["peer_answer"] alongside the
    original primary output. Confidence is updated to peer confidence.
    """
    base_output: dict[str, Any] = dict(result.output or {})
    base_output["peer_answer"] = peer.answer
    base_output["peer_confidence"] = peer.confidence
    base_output["peers_consulted"] = peer.peers_consulted
    base_output["aggregation_method"] = peer.aggregation_method
    return replace(result, output=base_output, confidence=peer.confidence)


# ------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------

def _conf_float(value: ConfidenceDecisionPort | float) -> float:
    if isinstance(value, ConfidenceDecisionPort):
        return float(value.confidence)
    return float(value)


def _extract_text(data: dict[str, Any]) -> str:
    for key in ("content", "text", "completion", "response"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message") or {}
            if isinstance(msg, dict):
                c = msg.get("content")
                if isinstance(c, str) and c.strip():
                    return c.strip()
            t = first.get("text")
            if isinstance(t, str) and t.strip():
                return t.strip()
    return ""


def _try_increment_counter(name: str) -> None:
    """Increment a Prometheus counter if prometheus_client is available."""
    try:
        from prometheus_client import Counter  # type: ignore[import]
        _counter_cache: dict[str, Any] = {}
        if name not in _counter_cache:
            _counter_cache[name] = Counter(name, f"BabyAI peer consultation metric: {name}")
        _counter_cache[name].inc()
    except Exception:
        pass
