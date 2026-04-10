"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-04-05 03:27:56
Description: Musixmatch authenticator — token management, 401 retry, and cooldown.
"""

import time
from typing import Optional
from urllib.parse import urlencode
import httpx
from loguru import logger

from .base import BaseAuthenticator
from ..cache import CacheEngine
from ..config import CredentialConfig, GeneralConfig, MUSIXMATCH_COOLDOWN_MS

_MUSIXMATCH_TOKEN_URL = "https://apic-desktop.musixmatch.com/ws/1.1/token.get"

_MXM_HEADERS = {"Cookie": "x-mxm-token-guid="}
_MXM_BASE_PARAMS = {
    "format": "json",
    "app_id": "web-desktop-app-v1.0",
}


def _new_mxm_client(timeout: float) -> httpx.AsyncClient:
    """Build Musixmatch client without httpx default User-Agent header."""
    client = httpx.AsyncClient(timeout=timeout, headers=_MXM_HEADERS)
    client.headers.pop("User-Agent", None)
    return client


class MusixmatchAuthenticator(BaseAuthenticator):
    def __init__(
        self, cache: CacheEngine, credentials: CredentialConfig, general: GeneralConfig
    ) -> None:
        super().__init__(cache, credentials, general)
        self._cached_token: Optional[str] = None
        self._cooldown_until_ms: int = 0

    @property
    def name(self) -> str:
        return "musixmatch"

    def is_configured(self) -> bool:
        return True  # anonymous token always available

    def is_cooldown(self) -> bool:
        """Return True if Musixmatch requests are blocked due to repeated auth failure."""
        now_ms = int(time.time() * 1000)
        if self._cooldown_until_ms > now_ms:
            return True
        data = self._cache.get_credential("musixmatch_cooldown")
        if data:
            until = data.get("until_ms", 0)
            if until > now_ms:
                self._cooldown_until_ms = until
                return True
        return False

    def _set_cooldown(self) -> None:
        now_ms = int(time.time() * 1000)
        until_ms = now_ms + MUSIXMATCH_COOLDOWN_MS
        self._cooldown_until_ms = until_ms
        self._cache.set_credential(
            "musixmatch_cooldown",
            {"until_ms": until_ms},
            expires_at_ms=until_ms,
        )
        logger.warning("Musixmatch: token unavailable, entering cooldown")

    def _invalidate_token(self) -> None:
        """Discard the current token from memory and DB."""
        self._cached_token = None
        # Store with an already-expired timestamp so get_credential returns None
        self._cache.set_credential("musixmatch", {"token": ""}, expires_at_ms=1)

    async def _fetch_new_token(self) -> Optional[str]:
        """Call token.get and persist the result. Returns token string or None."""
        params = {
            **_MXM_BASE_PARAMS,
            "user_language": "en",
            "t": str(int(time.time() * 1000)),
        }
        url = f"{_MUSIXMATCH_TOKEN_URL}?{urlencode(params)}"
        logger.debug("Musixmatch: fetching anonymous token")

        try:
            async with _new_mxm_client(self._general.http_timeout) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(f"Musixmatch: token fetch failed: {e}")
            return None

        token = (
            data.get("message", {}).get("body", {}).get("user_token")
            if isinstance(data, dict)
            else None
        )
        if not isinstance(token, str) or not token:
            logger.warning("Musixmatch: unexpected token.get response structure")
            return None

        self._cached_token = token
        # No expiry — token is valid until we get a 401
        self._cache.set_credential("musixmatch", {"token": token}, expires_at_ms=None)
        logger.debug("Musixmatch: obtained anonymous token")
        return token

    async def _get_token(self) -> Optional[str]:
        """Return a valid token: env var > memory > DB > fresh fetch."""
        if self._credentials.musixmatch_usertoken:
            return self._credentials.musixmatch_usertoken

        if self._cached_token:
            return self._cached_token

        data = self._cache.get_credential("musixmatch")
        if data and isinstance(data.get("token"), str) and data["token"]:
            self._cached_token = data["token"]
            return self._cached_token

        return await self._fetch_new_token()

    async def authenticate(self) -> Optional[str]:
        if self.is_cooldown():
            logger.debug("Musixmatch: authenticate called during cooldown")
            return None
        return await self._get_token()

    async def get_json(self, url_base: str, params: dict) -> Optional[dict]:
        """Authenticated GET to a Musixmatch endpoint.

        - Injects format, app_id, and usertoken automatically.
        - On 401: invalidates token, fetches a fresh one, retries once.
        - On failed token fetch (initial or retry): sets cooldown, returns None.
        - On network / HTTP error: raises (callers map this to NETWORK_ERROR).
        - Returns None if cooldown is active.
        """
        if self.is_cooldown():
            logger.debug("Musixmatch: request blocked by cooldown")
            return None

        token = await self._get_token()
        if not token:
            self._set_cooldown()
            return None

        async with _new_mxm_client(self._general.http_timeout) as client:
            url = f"{url_base}?{urlencode({**_MXM_BASE_PARAMS, **params, 'usertoken': token})}"
            resp = await client.get(url)

            if resp.status_code == 401:
                logger.debug("Musixmatch: 401 received, refreshing token")
                self._invalidate_token()
                token = await self._fetch_new_token()
                if not token:
                    self._set_cooldown()
                    return None
                url = f"{url_base}?{urlencode({**_MXM_BASE_PARAMS, **params, 'usertoken': token})}"
                resp = await client.get(url)

            resp.raise_for_status()
            return resp.json()
