"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-04-05 03:47:30
Description: QQ Music API authenticator - currently only a proxy.
"""

from typing import Optional

from .base import BaseAuthenticator
from ..config import credentials


class QQMusicAuthenticator(BaseAuthenticator):
    def __init__(self) -> None:
        pass

    @property
    def name(self) -> str:
        return "qqmusic"

    def is_configured(self) -> bool:
        return bool(credentials.QQ_MUSIC_API_URL)

    async def authenticate(self) -> Optional[str]:
        return credentials.QQ_MUSIC_API_URL
