"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 02:33:26
Description: Fetcher pipeline — registry and types
"""

from typing import Literal

from .base import BaseFetcher
from .local import LocalFetcher
from .cache_search import CacheSearchFetcher
from .spotify import SpotifyFetcher
from .lrclib import LrclibFetcher
from .lrclib_search import LrclibSearchFetcher
from .netease import NeteaseFetcher
from .qqmusic import QQMusicFetcher
from ..cache import CacheEngine

FetcherMethodType = Literal[
    "local",
    "cache-search",
    "spotify",
    "lrclib",
    "lrclib-search",
    "netease",
    "qqmusic",
]


def create_fetchers(cache: CacheEngine) -> dict[FetcherMethodType, BaseFetcher]:
    """Instantiate all fetchers. Returns a dict keyed by source name."""
    fetchers: dict[FetcherMethodType, BaseFetcher] = {
        "local": LocalFetcher(),
        "cache-search": CacheSearchFetcher(cache),
        "spotify": SpotifyFetcher(),
        "lrclib": LrclibFetcher(),
        "lrclib-search": LrclibSearchFetcher(),
        "netease": NeteaseFetcher(),
        "qqmusic": QQMusicFetcher(),
    }
    return fetchers
