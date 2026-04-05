"""
Author: Uyanide pywang0608@foxmail.com
Description: Credential authenticators for third-party provider APIs
"""

from lrx_cli.authenticators.qqmusic import QQMusicAuthenticator

from .base import BaseAuthenticator
from .spotify import SpotifyAuthenticator
from .musixmatch import MusixmatchAuthenticator
from .dummy import DummyAuthenticator

__all__ = [
    "BaseAuthenticator",
    "SpotifyAuthenticator",
    "MusixmatchAuthenticator",
    "QQMusicAuthenticator",
    "DummyAuthenticator",
]


def create_authenticators(cache) -> dict[str, BaseAuthenticator]:
    """Factory function to create authenticators with cache access."""
    return {
        "dummy": DummyAuthenticator(),
        "spotify": SpotifyAuthenticator(cache),
        "musixmatch": MusixmatchAuthenticator(cache),
        "qqmusic": QQMusicAuthenticator(),
    }
