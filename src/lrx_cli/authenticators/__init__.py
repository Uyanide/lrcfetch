"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-04-06 08:21:01
Description: Credential authenticators for third-party provider APIs
"""

from __future__ import annotations

from lrx_cli.authenticators.qqmusic import QQMusicAuthenticator

from .base import BaseAuthenticator
from .spotify import SpotifyAuthenticator
from .musixmatch import MusixmatchAuthenticator
from .dummy import DummyAuthenticator
from ..config import AppConfig

__all__ = [
    "BaseAuthenticator",
    "SpotifyAuthenticator",
    "MusixmatchAuthenticator",
    "QQMusicAuthenticator",
    "DummyAuthenticator",
]


def create_authenticators(cache, config: AppConfig) -> dict[str, BaseAuthenticator]:
    """Factory function to create authenticators with injected config."""
    return {
        "dummy": DummyAuthenticator(cache, config.credentials, config.general),
        "spotify": SpotifyAuthenticator(cache, config.credentials, config.general),
        "musixmatch": MusixmatchAuthenticator(
            cache, config.credentials, config.general
        ),
        "qqmusic": QQMusicAuthenticator(cache, config.credentials, config.general),
    }
