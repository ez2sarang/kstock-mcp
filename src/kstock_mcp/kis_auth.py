"""KIS access-token management: issue once, cache on disk (24h validity), separate paper/real.

KIS issues a token valid for one day; requesting again within six hours returns the same token
and sends the owner a notification, so the token is cached in memory and in an owner-only file
and reissued only when it is about to expire.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import httpx2

from .cache import read_json, write_json
from .config import Settings
from .errors import KStockError, network_error

KST = timezone(timedelta(hours=9))
_REFRESH_MARGIN_SEC = 600


class KISTokenManager:
    def __init__(self, settings: Settings, http: httpx2.Client, clock: Callable[[], float] = time.time):
        self._settings = settings
        self._http = http
        self._clock = clock
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0
        # Key the file by environment and a hash of the app key so paper/real/other keys never mix.
        digest = hashlib.sha256(settings.kis_app_key.encode()).hexdigest()[:12]
        self._cache_path = settings.cache_dir / f"kis_token_{settings.kis_env}_{digest}.json"

    def get_token(self) -> str:
        with self._lock:
            now = self._clock()
            if self._token and now < self._expires_at - _REFRESH_MARGIN_SEC:
                return self._token
            cached = read_json(self._cache_path)
            if isinstance(cached, dict) and cached.get("token") and now < float(cached.get("expires_at", 0)) - _REFRESH_MARGIN_SEC:
                self._token, self._expires_at = cached["token"], float(cached["expires_at"])
                return self._token
            return self._issue(now)

    def invalidate(self) -> None:
        with self._lock:
            self._token, self._expires_at = None, 0.0
            try:
                self._cache_path.unlink()
            except OSError:
                pass

    def _issue(self, now: float) -> str:
        self._settings.require_kis()
        try:
            resp = self._http.post(
                f"{self._settings.kis_base_url}/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._settings.kis_app_key,
                    "appsecret": self._settings.kis_app_secret,
                },
            )
        except httpx2.HTTPError as exc:
            raise network_error("KIS token", exc) from None
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code != 200 or not isinstance(body, dict) or not body.get("access_token"):
            code = body.get("error_code", resp.status_code) if isinstance(body, dict) else resp.status_code
            desc = body.get("error_description", "") if isinstance(body, dict) else ""
            hint = " (KIS allows about one token request per minute; wait and retry.)" if code == "EGW00133" else ""
            raise KStockError(f"KIS token request failed [{code}] {desc}".strip() + hint)
        self._token = body["access_token"]
        self._expires_at = _expiry_epoch(body, now)
        write_json(self._cache_path, {"token": self._token, "expires_at": self._expires_at})
        return self._token


def _expiry_epoch(body: dict, now: float) -> float:
    """Prefer KIS's absolute expiry (KST wall clock); fall back to expires_in seconds."""
    raw = body.get("access_token_token_expired")
    if raw:
        try:
            return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST).timestamp()
        except ValueError:
            pass
    try:
        return now + int(body.get("expires_in", 86400))
    except (TypeError, ValueError):
        return now + 86400
