from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from babyai.lora.models import AdapterCandidate

_DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "config" / "lora_registry.yaml"


class DisabledAdapterError(RuntimeError):
    def __init__(self, domain: str) -> None:
        super().__init__(f"LoRA adapter for domain '{domain}' is disabled in lora_registry.yaml")
        self.domain = domain


class LoRARegistry:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else _DEFAULT_REGISTRY_PATH
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            raise FileNotFoundError(f"LoRA registry not found: {self._path}")
        raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"LoRA registry must be a YAML mapping: {self._path}")
        if "adapters" not in raw:
            raise ValueError(f"LoRA registry missing 'adapters' key: {self._path}")
        if not isinstance(raw["adapters"], dict):
            raise ValueError(f"'adapters' must be a mapping: {self._path}")
        return raw

    @property
    def global_config(self) -> dict[str, Any]:
        return dict(self._data.get("global", {}))

    def get_adapter(self, domain: str) -> AdapterCandidate:
        adapters: dict[str, Any] = self._data["adapters"]
        if domain not in adapters:
            raise KeyError(f"Unknown LoRA domain: '{domain}'. Known domains: {sorted(adapters)}")
        entry = adapters[domain]
        if not entry.get("enabled", True):
            raise DisabledAdapterError(domain)
        return AdapterCandidate(
            candidate_id=domain,
            source_url=f"file://{entry['path']}",
            license="local",
            base_model=str(entry["base_model"]),
            param_count=0,
            last_updated=datetime.fromtimestamp(0, tz=timezone.utc),
            file_path=Path(entry["path"]),
            file_format="other",
        )

    def get_peer_domains(self, domain: str) -> list[str]:
        adapters: dict[str, Any] = self._data["adapters"]
        if domain not in adapters:
            raise KeyError(f"Unknown LoRA domain: '{domain}'. Known domains: {sorted(adapters)}")
        peers = adapters[domain].get("peer_domains", [])
        if not isinstance(peers, list):
            raise ValueError(f"'peer_domains' for '{domain}' must be a list")
        return [str(p) for p in peers]

    def get_confidence_threshold(self, domain: str) -> float:
        adapters: dict[str, Any] = self._data["adapters"]
        if domain not in adapters:
            raise KeyError(f"Unknown LoRA domain: '{domain}'")
        raw = adapters[domain].get(
            "confidence_threshold",
            self._data.get("global", {}).get("min_confidence_for_solo", 0.65),
        )
        return float(raw)

    def get_port(self, domain: str) -> int:
        adapters: dict[str, Any] = self._data["adapters"]
        if domain not in adapters:
            raise KeyError(f"Unknown LoRA domain: '{domain}'")
        return int(adapters[domain]["port"])

    def get_fallback_domain(self, domain: str) -> str:
        adapters: dict[str, Any] = self._data["adapters"]
        if domain not in adapters:
            raise KeyError(f"Unknown LoRA domain: '{domain}'")
        return str(adapters[domain]["fallback_domain"])

    def list_domains(self) -> list[str]:
        return sorted(self._data["adapters"])


def load_lora_registry(path: str | Path | None = None) -> LoRARegistry:
    return LoRARegistry(path=path)
