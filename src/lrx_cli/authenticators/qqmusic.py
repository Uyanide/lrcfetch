"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-04-05 03:47:30
Description: QQ Music API authenticator - currently only a proxy.
"""

from typing import Optional

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
