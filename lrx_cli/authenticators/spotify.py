"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-04-05 03:18:14
Description: Spotify authenticator — TOTP-based access token via SP_DC cookie.
"""

import hashlib
import hmac
import struct
import time
from typing import Optional, Tuple
import httpx
from loguru import logger

from .base import BaseAuthenticator
from ..cache import CacheEngine
from ..config import HTTP_TIMEOUT, UA_BROWSER, credentials

_SPOTIFY_TOKEN_URL = "https://open.spotify.com/api/token"
_SPOTIFY_SERVER_TIME_URL = "https://open.spotify.com/api/server-time"
_SPOTIFY_SECRET_URL = (
    "https://raw.githubusercontent.com/xyloflake/spot-secrets-go"
    "/refs/heads/main/secrets/secrets.json"
)
SPOTIFY_BASE_HEADERS = {
    "User-Agent": UA_BROWSER,
    "Referer": "https://open.spotify.com/",
    "Origin": "https://open.spotify.com",
    "App-Platform": "WebPlayer",
    "Spotify-App-Version": "1.2.88.21.g8e037c8f",
}


class SpotifyAuthenticator(BaseAuthenticator):
    def __init__(self, cache: CacheEngine) -> None:
        self._cache = cache
        self._cached_secret: Optional[Tuple[str, int]] = None
        self._cached_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    @property
    def name(self) -> str:
        return "spotify"

    def is_configured(self) -> bool:
        return bool(credentials.SPOTIFY_SP_DC)

    @staticmethod
    def _generate_totp(server_time_s: int, secret: str) -> str:
        counter = server_time_s // 30
        counter_bytes = struct.pack(">Q", counter)
        mac = hmac.new(secret.encode(), counter_bytes, hashlib.sha1).digest()
        offset = mac[-1] & 0x0F
        binary_code = (
            (mac[offset] & 0x7F) << 24
            | (mac[offset + 1] & 0xFF) << 16
            | (mac[offset + 2] & 0xFF) << 8
            | (mac[offset + 3] & 0xFF)
        )
        return str(binary_code % (10**6)).zfill(6)

    def _load_cached_token(self) -> Optional[str]:
        data = self._cache.get_credential("spotify")
        if not data:
            return None
        expires_ms = data.get("accessTokenExpirationTimestampMs", 0)
        if expires_ms <= int(time.time() * 1000):
            logger.debug("Spotify: persisted token expired")
            return None
        token = data.get("accessToken", "")
        if not token:
            return None
        self._cached_token = token
        self._token_expires_at = expires_ms / 1000.0
        logger.debug("Spotify: loaded token from DB cache")
        return token

    def _save_token(self, body: dict) -> None:
        expires_ms = body.get("accessTokenExpirationTimestampMs")
        self._cache.set_credential("spotify", body, expires_ms)
        logger.debug("Spotify: token saved to DB cache")

    async def _get_server_time(self, client: httpx.AsyncClient) -> Optional[int]:
        try:
            res = await client.get(_SPOTIFY_SERVER_TIME_URL, timeout=HTTP_TIMEOUT)
            res.raise_for_status()
            data = res.json()
            if not isinstance(data, dict) or "serverTime" not in data:
                logger.error(f"Spotify: unexpected server-time response: {data}")
                return None
            server_time = data["serverTime"]
            logger.debug(f"Spotify: server time = {server_time}")
            return server_time
        except Exception as e:
            logger.error(f"Spotify: failed to fetch server time: {e}")
            return None

    async def _get_secret(self, client: httpx.AsyncClient) -> Optional[Tuple[str, int]]:
        if self._cached_secret is not None:
            logger.debug("Spotify: using cached TOTP secret")
            return self._cached_secret
        try:
            res = await client.get(_SPOTIFY_SECRET_URL, timeout=HTTP_TIMEOUT)
            res.raise_for_status()
            data = res.json()
            if not isinstance(data, list) or len(data) == 0:
                logger.error(
                    f"Spotify: unexpected secrets response (type={type(data).__name__})"
                )
                return None
            last = data[-1]
            if "secret" not in last or "version" not in last:
                logger.error(f"Spotify: malformed secret entry: {list(last.keys())}")
                return None
            secret_raw = last["secret"]
            version = last["version"]
            secret = "".join(
                str(ord(c) ^ ((i % 33) + 9)) for i, c in enumerate(secret_raw)
            )
            logger.debug(f"Spotify: decoded secret v{version} (len={len(secret)})")
            self._cached_secret = (secret, version)
            return self._cached_secret
        except Exception as e:
            logger.error(f"Spotify: failed to fetch secret: {e}")
            return None

    async def authenticate(self) -> Optional[str]:
        if self._cached_token and time.time() < self._token_expires_at - 30:
            logger.debug("Spotify: using in-memory cached token")
            return self._cached_token

        db_token = self._load_cached_token()
        if db_token and time.time() < self._token_expires_at - 30:
            return db_token

        if not credentials.SPOTIFY_SP_DC:
            logger.error("Spotify: SPOTIFY_SP_DC env var not set — cannot authenticate")
            return None

        headers = {
            "Accept": "*/*",
            "Cookie": f"sp_dc={credentials.SPOTIFY_SP_DC}",
            **SPOTIFY_BASE_HEADERS,
        }

        async with httpx.AsyncClient(headers=headers) as client:
            server_time = await self._get_server_time(client)
            if server_time is None:
                return None

            secret_data = await self._get_secret(client)
            if secret_data is None:
                return None

            secret, version = secret_data
            totp = self._generate_totp(server_time, secret)
            logger.debug(f"Spotify: generated TOTP v{version}: {totp}")

            params = {
                "reason": "init",
                "productType": "web-player",
                "totp": totp,
                "totpVer": str(version),
                "totpServer": totp,
            }

            try:
                res = await client.get(
                    _SPOTIFY_TOKEN_URL, params=params, timeout=HTTP_TIMEOUT
                )
                if res.status_code != 200:
                    logger.error(f"Spotify: token request returned {res.status_code}")
                    return None

                body = res.json()
                if not isinstance(body, dict) or "accessToken" not in body:
                    logger.error(
                        f"Spotify: unexpected token response keys: {list(body.keys()) if isinstance(body, dict) else type(body).__name__}"
                    )
                    return None

                token = body["accessToken"]
                if body.get("isAnonymous", False):
                    logger.warning(
                        "Spotify: received anonymous token — SP_DC may be invalid"
                    )

                expires_ms = body.get("accessTokenExpirationTimestampMs", 0)
                if expires_ms and expires_ms > int(time.time() * 1000):
                    self._token_expires_at = expires_ms / 1000.0
                else:
                    logger.warning("Spotify: token expiry missing or invalid")
                    self._token_expires_at = time.time() + 3600

                self._cached_token = token
                self._save_token(body)
                logger.debug("Spotify: obtained access token")
                return token

            except Exception as e:
                logger.error(f"Spotify: token request failed: {e}")
                return None
