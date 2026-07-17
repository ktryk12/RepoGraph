"""
services/publisher/token_manager.py — OAuth token lifecycle manager.

Håndterer:
  - LinkedIn: refresh access_token via refresh_token (udløber hver 60 dage)
  - TikTok:   OAuth2 authorization-code flow + token refresh
  - YouTube:  refresh via google-auth-oauthlib
  - Twitter:  statiske env-var tokens (OAuth 1.0a — ingen refresh nødvendig)

Tokens gemmes i Redis (key: "publisher:token:<channel>") med TTL = expires_in - 60s.
Fallback: filbaseret token-store i PUBLISHER_TOKEN_DIR (default: artifacts/publisher/tokens/).

Brug:

    from services.publisher.token_manager import TokenManager
    mgr = TokenManager()
    token = mgr.get_token("linkedin")   # returnerer gyldigt access_token eller None
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

_log = logging.getLogger("publisher.token_manager")

_TOKEN_DIR = Path(os.getenv("PUBLISHER_TOKEN_DIR", "artifacts/publisher/tokens"))

# LinkedIn OAuth2
_LINKEDIN_CLIENT_ID     = os.getenv("LINKEDIN_CLIENT_ID", "")
_LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET", "")
_LINKEDIN_REFRESH_TOKEN = os.getenv("LINKEDIN_REFRESH_TOKEN", "")
_LINKEDIN_ACCESS_TOKEN  = os.getenv("LINKEDIN_ACCESS_TOKEN", "")

# TikTok OAuth2
_TIKTOK_CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY", "")
_TIKTOK_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")
_TIKTOK_REDIRECT_URI  = os.getenv("TIKTOK_REDIRECT_URI", "")

# YouTube (Google OAuth2)
_YOUTUBE_CLIENT_SECRET_FILE = os.getenv("YOUTUBE_CLIENT_SECRET_FILE", "")

# Redis (optional — used as fast token cache)
_REDIS_URL = os.getenv("PUBLISHER_REDIS_URL", os.getenv("REDIS_URL", ""))


class TokenManager:
    """
    Manages OAuth tokens for all publisher channels.

    Redis is optional: if unavailable, tokens are cached to disk under
    PUBLISHER_TOKEN_DIR. Either way, a valid token is returned or None.
    """

    def __init__(self) -> None:
        self._redis = self._connect_redis()
        _TOKEN_DIR.mkdir(parents=True, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_token(self, channel: str) -> Optional[str]:
        """
        Return a valid access token for `channel`, refreshing if needed.
        Returns None if no credentials are configured or refresh fails.
        """
        cached = self._load_cached(channel)
        if cached and not self._is_expired(cached):
            return cached["access_token"]

        refreshed = self._refresh(channel)
        if refreshed:
            self._save_cached(channel, refreshed)
            return refreshed["access_token"]

        # Fall back to env-var token (may be stale)
        return self._env_token(channel)

    def get_authorization_url(self, channel: str, state: str = "") -> Optional[str]:
        """
        Return OAuth2 authorization URL for initial user consent.
        Used by UI /keys onboarding wizard.
        Returns None if channel doesn't support OAuth2 redirect flow.
        """
        if channel == "tiktok":
            return self._tiktok_auth_url(state)
        if channel == "linkedin":
            return self._linkedin_auth_url(state)
        if channel == "youtube":
            return self._youtube_auth_url(state)
        return None

    def exchange_code(self, channel: str, code: str) -> Optional[Dict[str, Any]]:
        """
        Exchange authorization code for tokens (callback endpoint).
        Saves tokens and returns token dict, or None on failure.
        """
        result = None
        if channel == "tiktok":
            result = self._tiktok_exchange_code(code)
        elif channel == "linkedin":
            result = self._linkedin_exchange_code(code)
        elif channel == "youtube":
            result = self._youtube_exchange_code(code)

        if result:
            self._save_cached(channel, result)
        return result

    # ── LinkedIn ──────────────────────────────────────────────────────────────

    def _linkedin_auth_url(self, state: str) -> str:
        import urllib.parse
        params = {
            "response_type": "code",
            "client_id": _LINKEDIN_CLIENT_ID,
            "redirect_uri": os.getenv("LINKEDIN_REDIRECT_URI", ""),
            "state": state,
            "scope": "w_member_social w_organization_social r_organization_social",
        }
        return "https://www.linkedin.com/oauth/v2/authorization?" + urllib.parse.urlencode(params)

    def _linkedin_exchange_code(self, code: str) -> Optional[Dict[str, Any]]:
        if not _LINKEDIN_CLIENT_ID or not _LINKEDIN_CLIENT_SECRET:
            return None
        try:
            import requests
            resp = requests.post(
                "https://www.linkedin.com/oauth/v2/accessToken",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": os.getenv("LINKEDIN_REDIRECT_URI", ""),
                    "client_id": _LINKEDIN_CLIENT_ID,
                    "client_secret": _LINKEDIN_CLIENT_SECRET,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            data["obtained_at"] = time.time()
            return data
        except Exception as exc:
            _log.warning("linkedin_code_exchange_failed error=%s", exc)
            return None

    def _linkedin_refresh(self) -> Optional[Dict[str, Any]]:
        """Refresh LinkedIn access_token using refresh_token (valid 365 days)."""
        refresh_token = self._load_cached("linkedin", key="refresh_token") or _LINKEDIN_REFRESH_TOKEN
        if not refresh_token or not _LINKEDIN_CLIENT_ID or not _LINKEDIN_CLIENT_SECRET:
            return None
        try:
            import requests
            resp = requests.post(
                "https://www.linkedin.com/oauth/v2/accessToken",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": _LINKEDIN_CLIENT_ID,
                    "client_secret": _LINKEDIN_CLIENT_SECRET,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            data["obtained_at"] = time.time()
            _log.info("linkedin_token_refreshed expires_in=%s", data.get("expires_in"))
            return data
        except Exception as exc:
            _log.warning("linkedin_token_refresh_failed error=%s", exc)
            return None

    # ── TikTok ────────────────────────────────────────────────────────────────

    def _tiktok_auth_url(self, state: str) -> str:
        import urllib.parse
        params = {
            "client_key": _TIKTOK_CLIENT_KEY,
            "response_type": "code",
            "scope": "video.upload,video.publish",
            "redirect_uri": _TIKTOK_REDIRECT_URI,
            "state": state,
        }
        return "https://www.tiktok.com/v2/auth/authorize/?" + urllib.parse.urlencode(params)

    def _tiktok_exchange_code(self, code: str) -> Optional[Dict[str, Any]]:
        if not _TIKTOK_CLIENT_KEY or not _TIKTOK_CLIENT_SECRET:
            return None
        try:
            import requests
            resp = requests.post(
                "https://open.tiktokapis.com/v2/oauth/token/",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "client_key": _TIKTOK_CLIENT_KEY,
                    "client_secret": _TIKTOK_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": _TIKTOK_REDIRECT_URI,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json().get("data", resp.json())
            data["obtained_at"] = time.time()
            return data
        except Exception as exc:
            _log.warning("tiktok_code_exchange_failed error=%s", exc)
            return None

    def _tiktok_refresh(self) -> Optional[Dict[str, Any]]:
        cached = self._load_cached("tiktok")
        refresh_token = (cached or {}).get("refresh_token", "")
        if not refresh_token or not _TIKTOK_CLIENT_KEY or not _TIKTOK_CLIENT_SECRET:
            return None
        try:
            import requests
            resp = requests.post(
                "https://open.tiktokapis.com/v2/oauth/token/",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "client_key": _TIKTOK_CLIENT_KEY,
                    "client_secret": _TIKTOK_CLIENT_SECRET,
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json().get("data", resp.json())
            data["obtained_at"] = time.time()
            _log.info("tiktok_token_refreshed")
            return data
        except Exception as exc:
            _log.warning("tiktok_token_refresh_failed error=%s", exc)
            return None

    # ── YouTube ───────────────────────────────────────────────────────────────

    def _youtube_auth_url(self, state: str) -> Optional[str]:
        if not _YOUTUBE_CLIENT_SECRET_FILE:
            return None
        try:
            from google_auth_oauthlib.flow import Flow  # type: ignore[import]
            flow = Flow.from_client_secrets_file(
                _YOUTUBE_CLIENT_SECRET_FILE,
                scopes=["https://www.googleapis.com/auth/youtube.upload"],
                redirect_uri=os.getenv("YOUTUBE_REDIRECT_URI", ""),
            )
            url, _ = flow.authorization_url(state=state, access_type="offline", prompt="consent")
            return url
        except Exception as exc:
            _log.warning("youtube_auth_url_failed error=%s", exc)
            return None

    def _youtube_exchange_code(self, code: str) -> Optional[Dict[str, Any]]:
        if not _YOUTUBE_CLIENT_SECRET_FILE:
            return None
        try:
            from google_auth_oauthlib.flow import Flow  # type: ignore[import]
            flow = Flow.from_client_secrets_file(
                _YOUTUBE_CLIENT_SECRET_FILE,
                scopes=["https://www.googleapis.com/auth/youtube.upload"],
                redirect_uri=os.getenv("YOUTUBE_REDIRECT_URI", ""),
            )
            flow.fetch_token(code=code)
            creds = flow.credentials
            data = {
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
                "expires_in": 3600,
                "obtained_at": time.time(),
            }
            return data
        except Exception as exc:
            _log.warning("youtube_code_exchange_failed error=%s", exc)
            return None

    def _youtube_refresh(self) -> Optional[Dict[str, Any]]:
        cached = self._load_cached("youtube")
        if not cached or not _YOUTUBE_CLIENT_SECRET_FILE:
            return None
        try:
            import google.oauth2.credentials as _creds  # type: ignore[import]
            import google.auth.transport.requests as _transport  # type: ignore[import]
            creds = _creds.Credentials(
                token=cached.get("access_token"),
                refresh_token=cached.get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=cached.get("client_id", ""),
                client_secret=cached.get("client_secret", ""),
            )
            creds.refresh(_transport.Request())
            data = {**cached, "access_token": creds.token, "obtained_at": time.time()}
            _log.info("youtube_token_refreshed")
            return data
        except Exception as exc:
            _log.warning("youtube_token_refresh_failed error=%s", exc)
            return None

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def _refresh(self, channel: str) -> Optional[Dict[str, Any]]:
        if channel == "linkedin":
            return self._linkedin_refresh()
        if channel == "tiktok":
            return self._tiktok_refresh()
        if channel == "youtube":
            return self._youtube_refresh()
        return None

    def _env_token(self, channel: str) -> Optional[str]:
        mapping = {
            "linkedin": _LINKEDIN_ACCESS_TOKEN,
            "twitter": os.getenv("TWITTER_ACCESS_TOKEN", ""),
            "youtube": os.getenv("YOUTUBE_API_KEY", ""),
        }
        return mapping.get(channel) or None

    # ── Storage ───────────────────────────────────────────────────────────────

    def _is_expired(self, token_data: Dict[str, Any], buffer_seconds: int = 120) -> bool:
        obtained = token_data.get("obtained_at", 0.0)
        expires_in = token_data.get("expires_in", 0)
        if not expires_in:
            return False
        return time.time() >= (obtained + expires_in - buffer_seconds)

    def _redis_key(self, channel: str) -> str:
        return f"publisher:token:{channel}"

    def _load_cached(self, channel: str, key: Optional[str] = None) -> Optional[Any]:
        raw = None
        if self._redis:
            try:
                raw = self._redis.get(self._redis_key(channel))
            except Exception:
                pass
        if not raw:
            path = _TOKEN_DIR / f"{channel}.json"
            if path.exists():
                raw = path.read_text(encoding="utf-8")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data.get(key) if key else data
        except Exception:
            return None

    def _save_cached(self, channel: str, token_data: Dict[str, Any]) -> None:
        payload = json.dumps(token_data)
        expires_in = int(token_data.get("expires_in", 3600))
        if self._redis:
            try:
                self._redis.setex(self._redis_key(channel), max(expires_in - 60, 60), payload)
            except Exception:
                pass
        try:
            path = _TOKEN_DIR / f"{channel}.json"
            path.write_text(payload, encoding="utf-8")
        except Exception as exc:
            _log.warning("token_manager_save_file_failed channel=%s error=%s", channel, exc)

    def _connect_redis(self) -> Optional[Any]:
        if not _REDIS_URL:
            return None
        try:
            import redis
            client = redis.from_url(_REDIS_URL, decode_responses=True)
            client.ping()
            return client
        except Exception:
            return None
