"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-04-05 03:47:30
Description: QQ Music API authenticator - currently only a proxy.
"""

from __future__ import annotations

from typing import Optional
import httpx
from loguru import logger

from .base import BaseAuthenticator
from ..cache import CacheEngine
from ..config import CredentialConfig, GeneralConfig


class QQMusicAuthenticator(BaseAuthenticator):
    def __init__(
        self, cache: CacheEngine, credentials: CredentialConfig, general: GeneralConfig
    ) -> None:
        super().__init__(cache, credentials, general)

    @property
    def name(self) -> str:
        return "qqmusic"

    def is_configured(self) -> bool:
        return bool(self._credentials.qq_music_api_url)

    async def authenticate(self) -> Optional[str]:
        return self._credentials.qq_music_api_url.rstrip("/") or None

    async def search(self, keyword: str, num: int) -> dict | None:
        """Call qq-music-api search endpoint and return raw JSON payload."""
        base_url = await self.authenticate()
        if not base_url:
            return None

        try:
            async with httpx.AsyncClient(timeout=self._general.http_timeout) as client:
                resp = await client.get(
                    f"{base_url}/api/search",
                    params={"keyword": keyword, "type": "song", "num": num},
                )
                resp.raise_for_status()
                data = resp.json()
            if not isinstance(data, dict):
                return None
            return data
        except Exception as e:
            logger.error(f"QQMusic: search request failed: {e}")
            return None

    async def get_lyric(self, mid: str) -> dict | None:
        """Call qq-music-api lyric endpoint and return raw JSON payload."""
        base_url = await self.authenticate()
        if not base_url:
            return None

        try:
            async with httpx.AsyncClient(timeout=self._general.http_timeout) as client:
                resp = await client.get(
                    f"{base_url}/api/lyric",
                    params={"mid": mid},
                )
                resp.raise_for_status()
                data = resp.json()
            if not isinstance(data, dict):
                return None
            return data
        except Exception as e:
            logger.error(f"QQMusic: lyric request failed for mid={mid}: {e}")
            return None
