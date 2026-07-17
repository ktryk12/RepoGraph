from __future__ import annotations

import logging
import socket
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from babyai.lora.registry_loader import LoRARegistry, load_lora_registry

logger = logging.getLogger(__name__)

_FALLBACK_URL = "http://model-runner:8081"
_HEALTH_TIMEOUT_SECONDS = 3.0
_HEALTH_PATHS = ("/health", "/v1/models")


class DomainRouter:
    """Maps LoRA domains to per-ekspert llama.cpp endpoints.

    Bruger lora_registry.yaml (port per domæne) til at konstruere URLs på
    formen http://model-runner-{domain}:{port}.

    Fallback til http://model-runner:8081 ved ukendt eller disabled domæne.
    """

    def __init__(
        self,
        *,
        registry: LoRARegistry | None = None,
        fallback_url: str = _FALLBACK_URL,
    ) -> None:
        self._registry = registry or _try_load_registry()
        self._fallback_url = str(fallback_url).rstrip("/")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_endpoint(self, domain: str) -> str:
        """Returnér endpoint URL for domænet.

        Returnerer fallback URL uden exception ved ukendt/disabled domæne.
        """
        if self._registry is None:
            return self._fallback_url
        try:
            port = self._registry.get_port(domain)
            return f"http://model-runner-{domain}:{port}"
        except KeyError:
            logger.debug("domain_router_unknown_domain domain=%s using_fallback=%s", domain, self._fallback_url)
            return self._fallback_url

    def is_healthy(self, domain: str) -> bool:
        """Ping helbredscheck for domænets endpoint. Aldrig exception."""
        endpoint = self.get_endpoint(domain)
        for path in _HEALTH_PATHS:
            try:
                url = f"{endpoint}{path}"
                with urlopen(url, timeout=_HEALTH_TIMEOUT_SECONDS) as resp:
                    if int(resp.getcode()) < 400:
                        return True
            except HTTPError as exc:
                if int(exc.code) < 500:
                    return True
            except Exception:
                pass
        logger.debug("domain_router_unhealthy domain=%s endpoint=%s", domain, endpoint)
        return False

    def get_all_endpoints(self) -> dict[str, str]:
        """Returnér alle aktive domæne → endpoint mappings."""
        if self._registry is None:
            return {"default": self._fallback_url}
        result: dict[str, str] = {}
        for domain in self._registry.list_domains():
            try:
                self._registry.get_adapter(domain)  # raises DisabledAdapterError
                result[domain] = self.get_endpoint(domain)
            except Exception:
                pass
        return result


# ------------------------------------------------------------------
# Module-level convenience
# ------------------------------------------------------------------

def _try_load_registry() -> LoRARegistry | None:
    try:
        return load_lora_registry()
    except FileNotFoundError:
        return None


def build_domain_router(registry: LoRARegistry | None = None) -> DomainRouter:
    return DomainRouter(registry=registry)
