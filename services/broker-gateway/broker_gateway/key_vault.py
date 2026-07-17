"""
broker_gateway/key_vault.py — BYOK API-key store backed by HashiCorp Vault.

Each customer's exchange API credentials are stored at:
    secret/data/byok/{customer_id}/{exchange}

Expected secret keys:
    api_key    — exchange API key
    api_secret — exchange API secret / passphrase
    subaccount — optional subaccount name (Bybit etc.)

Dev fallback: if Vault is unreachable (or VAULT_ADDR is unset), keys are read
from environment variables:
    BYOK_{CUSTOMER_ID}_{EXCHANGE}_API_KEY
    BYOK_{CUSTOMER_ID}_{EXCHANGE}_API_SECRET

The fallback is intentionally loud (WARNING log) so it is never silently used
in production.

Usage:
    vault = KeyVault()
    creds = vault.get_credentials("cust-123", "binance")
    # creds.api_key, creds.api_secret
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

_log = logging.getLogger("broker_gateway.key_vault")

_VAULT_ADDR  = os.getenv("VAULT_ADDR", "http://vault:8200")
_VAULT_TOKEN = os.getenv("VAULT_TOKEN", "")
_VAULT_MOUNT = os.getenv("VAULT_MOUNT", "secret")


@dataclass(frozen=True)
class ExchangeCredentials:
    customer_id: str
    exchange: str
    api_key: str
    api_secret: str
    subaccount: Optional[str] = None

    def is_valid(self) -> bool:
        return bool(self.api_key and self.api_secret)


class KeyVault:
    """
    BYOK credential store.

    Thread-safe: hvac client is stateless between calls.
    Keys are never logged or stored in instance state beyond the
    returned ExchangeCredentials object.
    """

    def __init__(self) -> None:
        self._client = self._connect()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_credentials(self, customer_id: str, exchange: str) -> Optional[ExchangeCredentials]:
        """
        Return credentials for (customer_id, exchange), or None if not found.
        Tries Vault first, falls back to env vars in dev.
        """
        creds = self._from_vault(customer_id, exchange)
        if creds and creds.is_valid():
            return creds

        creds = self._from_env(customer_id, exchange)
        if creds and creds.is_valid():
            _log.warning(
                "key_vault_env_fallback customer_id=%s exchange=%s "
                "— set VAULT_ADDR/VAULT_TOKEN for production",
                customer_id, exchange,
            )
            return creds

        _log.error("key_vault_no_credentials customer_id=%s exchange=%s", customer_id, exchange)
        return None

    def store_credentials(
        self,
        customer_id: str,
        exchange: str,
        api_key: str,
        api_secret: str,
        subaccount: Optional[str] = None,
    ) -> bool:
        """
        Write credentials to Vault KV v2. Returns True on success.
        Only available when Vault is reachable.
        """
        if not self._client:
            _log.error("key_vault_store_failed_no_vault customer_id=%s exchange=%s", customer_id, exchange)
            return False
        try:
            path = self._vault_path(customer_id, exchange)
            data = {"api_key": api_key, "api_secret": api_secret}
            if subaccount:
                data["subaccount"] = subaccount
            self._client.secrets.kv.v2.create_or_update_secret(
                path=path,
                secret=data,
                mount_point=_VAULT_MOUNT,
            )
            _log.info("key_vault_stored customer_id=%s exchange=%s", customer_id, exchange)
            return True
        except Exception as exc:
            _log.error("key_vault_store_error customer_id=%s exchange=%s error=%s", customer_id, exchange, exc)
            return False

    def delete_credentials(self, customer_id: str, exchange: str) -> bool:
        """Permanently delete all versions of the secret from Vault."""
        if not self._client:
            return False
        try:
            path = self._vault_path(customer_id, exchange)
            self._client.secrets.kv.v2.delete_metadata_and_all_versions(
                path=path,
                mount_point=_VAULT_MOUNT,
            )
            _log.info("key_vault_deleted customer_id=%s exchange=%s", customer_id, exchange)
            return True
        except Exception as exc:
            _log.error("key_vault_delete_error customer_id=%s exchange=%s error=%s", customer_id, exchange, exc)
            return False

    # ── Internal ──────────────────────────────────────────────────────────────

    def _vault_path(self, customer_id: str, exchange: str) -> str:
        return f"byok/{customer_id}/{exchange}"

    def _from_vault(self, customer_id: str, exchange: str) -> Optional[ExchangeCredentials]:
        if not self._client:
            return None
        try:
            path = self._vault_path(customer_id, exchange)
            resp = self._client.secrets.kv.v2.read_secret_version(
                path=path,
                mount_point=_VAULT_MOUNT,
                raise_on_deleted_version=True,
            )
            data = resp["data"]["data"]
            return ExchangeCredentials(
                customer_id=customer_id,
                exchange=exchange,
                api_key=data.get("api_key", ""),
                api_secret=data.get("api_secret", ""),
                subaccount=data.get("subaccount"),
            )
        except Exception as exc:
            _log.debug("key_vault_vault_miss customer_id=%s exchange=%s error=%s", customer_id, exchange, exc)
            return None

    def _from_env(self, customer_id: str, exchange: str) -> Optional[ExchangeCredentials]:
        prefix = f"BYOK_{customer_id.upper().replace('-', '_')}_{exchange.upper()}"
        api_key    = os.getenv(f"{prefix}_API_KEY", "")
        api_secret = os.getenv(f"{prefix}_API_SECRET", "")
        if not api_key:
            return None
        return ExchangeCredentials(
            customer_id=customer_id,
            exchange=exchange,
            api_key=api_key,
            api_secret=api_secret,
        )

    def _connect(self):
        if not _VAULT_ADDR or not _VAULT_TOKEN:
            _log.info("key_vault_vault_disabled — no VAULT_ADDR or VAULT_TOKEN")
            return None
        try:
            import hvac  # type: ignore[import]
            client = hvac.Client(url=_VAULT_ADDR, token=_VAULT_TOKEN)
            if client.is_authenticated():
                _log.info("key_vault_connected vault_addr=%s", _VAULT_ADDR)
                return client
            _log.warning("key_vault_auth_failed vault_addr=%s", _VAULT_ADDR)
            return None
        except Exception as exc:
            _log.warning("key_vault_connect_error vault_addr=%s error=%s", _VAULT_ADDR, exc)
            return None
