from __future__ import annotations

import json
import logging
import socket
import time
from pathlib import Path
from typing import Callable, Dict, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# Matches the type alias in model_runner_http.py
_RequestFn = Callable[[str, str, Dict[str, Any] | None, float], Dict[str, Any]]

_DEFAULT_TIMEOUT_SECONDS = 10.0
_DEFAULT_RELOAD_DIR = Path("/data/lora_reload")

# llama.cpp slot endpoint — 404/501 means server built without --slots support
_SLOT_PATH = "/slots/0"
_SLOT_UNSUPPORTED_CODES = {404, 501}


class HotReloadError(RuntimeError):
    """Raised when all hot-reload strategies are exhausted."""

    def __init__(self, domain: str, reason: str) -> None:
        super().__init__(f"lora_hot_reload_failed domain={domain} reason={reason}")
        self.domain = domain
        self.reason = reason


class LlamaSlotReloader:
    """Attempts an in-process LoRA swap via the llama.cpp /slots API.

    Returns True on success, False when the server does not support slots
    (HTTP 404 or 501).  Raises HotReloadError on unexpected failures.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        request_fn: _RequestFn | None = None,
    ) -> None:
        self._timeout_seconds = max(0.1, float(timeout_seconds))
        self._request_fn = request_fn

    def try_slot_reload(self, base_url: str, adapter_path: str) -> bool:
        base = str(base_url).rstrip("/")
        payload = {"lora_path": str(adapter_path)}
        try:
            self._post_json(base, _SLOT_PATH, payload)
            logger.info(
                "lora_slot_reload_ok base_url=%s adapter_path=%s",
                base_url,
                adapter_path,
            )
            return True
        except _SlotUnsupportedError:
            logger.info(
                "lora_slot_reload_unsupported base_url=%s", base_url
            )
            return False
        except HotReloadError:
            raise
        except Exception as exc:
            raise HotReloadError(
                domain="unknown",
                reason=f"slot_reload_unexpected: {exc}",
            ) from exc

    def _post_json(self, base_url: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self._request_fn is not None:
            try:
                response = self._request_fn("POST", path, payload, self._timeout_seconds)
            except HTTPError as exc:
                if exc.code in _SLOT_UNSUPPORTED_CODES:
                    raise _SlotUnsupportedError(exc.code) from exc
                body_text = exc.read().decode("utf-8", errors="replace")
                raise HotReloadError(
                    "unknown",
                    f"slot_http_error status={exc.code} body={body_text}",
                ) from exc
            if not isinstance(response, dict):
                raise HotReloadError("unknown", "slot_response_not_dict")
            return response

        body = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        req = Request(
            url=f"{base_url}{path}",
            data=body,
            headers={"content-type": "application/json", "accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=self._timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace").strip()
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            if exc.code in _SLOT_UNSUPPORTED_CODES:
                raise _SlotUnsupportedError(exc.code) from exc
            body_text = exc.read().decode("utf-8", errors="replace")
            raise HotReloadError(
                "unknown",
                f"slot_http_error status={exc.code} body={body_text}",
            ) from exc
        except TimeoutError as exc:
            raise HotReloadError(
                "unknown",
                f"slot_timeout timeout_seconds={self._timeout_seconds}",
            ) from exc
        except URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise HotReloadError(
                    "unknown",
                    f"slot_timeout timeout_seconds={self._timeout_seconds}",
                ) from exc
            raise HotReloadError("unknown", f"slot_unreachable: {exc}") from exc


class LlamaProcessRestarter:
    """Signals a LoRA reload by writing a trigger file to a shared volume.

    The llama.cpp model-runner container is expected to be wrapped with a
    script that watches ``reload_dir`` for ``{domain}.trigger`` files and
    restarts the server process with the new ``--lora`` argument.

    Writing the file is synchronous and always succeeds as long as the
    directory is writable — the actual process restart is asynchronous.
    """

    def __init__(self, reload_dir: str | Path = _DEFAULT_RELOAD_DIR) -> None:
        self._reload_dir = Path(reload_dir)

    def restart_with_lora(self, domain: str, adapter_path: str) -> bool:
        try:
            self._reload_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HotReloadError(
                domain,
                f"restart_mkdir_failed: {exc}",
            ) from exc

        trigger = self._reload_dir / f"{domain}.trigger"
        payload = json.dumps(
            {"domain": domain, "adapter_path": str(adapter_path), "ts": time.time()},
            ensure_ascii=True,
        )
        try:
            trigger.write_text(payload, encoding="utf-8")
        except OSError as exc:
            raise HotReloadError(
                domain,
                f"restart_write_failed path={trigger}: {exc}",
            ) from exc

        logger.info(
            "lora_restart_trigger_written domain=%s trigger=%s adapter_path=%s",
            domain,
            trigger,
            adapter_path,
        )
        return True


class LoRAHotReloader:
    """Orchestrates hot-reload using the strategy from lora_registry.yaml.

    Strategy 'slot'    → try slot reload; fall back to restart on False.
    Strategy 'restart' → go directly to process restart.

    Raises HotReloadError if the chosen (and fallback) strategy fails.
    """

    def __init__(
        self,
        *,
        strategy: str = "restart",
        slot_reloader: LlamaSlotReloader | None = None,
        restarter: LlamaProcessRestarter | None = None,
    ) -> None:
        if strategy not in {"slot", "restart"}:
            raise ValueError(
                f"hot_reload_strategy must be 'slot' or 'restart', got '{strategy}'"
            )
        self._strategy = strategy
        self._slot_reloader = slot_reloader or LlamaSlotReloader()
        self._restarter = restarter or LlamaProcessRestarter()

    def reload(self, domain: str, base_url: str, adapter_path: str) -> None:
        if self._strategy == "slot":
            try:
                reloaded = self._slot_reloader.try_slot_reload(base_url, adapter_path)
            except HotReloadError as exc:
                logger.warning(
                    "lora_slot_reload_error domain=%s error=%s; falling back to restart",
                    domain,
                    exc,
                )
                reloaded = False

            if reloaded:
                return

            logger.info(
                "lora_slot_unsupported_falling_back domain=%s strategy=restart",
                domain,
            )

        # 'restart' strategy or slot fallback
        self._restarter.restart_with_lora(domain, adapter_path)


class _SlotUnsupportedError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"slot_unsupported http_code={code}")
        self.code = code
