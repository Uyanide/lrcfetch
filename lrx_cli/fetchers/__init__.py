"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 02:33:26
Description: Fetcher pipeline — registry and types
"""

from typing import Literal, Optional
from loguru import logger

from .base import BaseFetcher
from .local import LocalFetcher
from .cache_search import CacheSearchFetcher
from .spotify import SpotifyFetcher
from .lrclib import LrclibFetcher
from .lrclib_search import LrclibSearchFetcher
from .musixmatch import MusixmatchFetcher, MusixmatchSpotifyFetcher
from .netease import NeteaseFetcher
from .qqmusic import QQMusicFetcher
from ..cache import CacheEngine
from ..models import TrackMeta

FetcherMethodType = Literal[
    "local",
    "cache-search",
    "spotify",
    "lrclib",
    "musixmatch-spotify",
    "lrclib-search",
    "netease",
    "qqmusic",
    "musixmatch",
]

# Fetchers within a group run in parallel; groups run sequentially.
# A group that produces any trusted and synced result stops the pipeline.
_FETCHER_GROUPS: list[list[FetcherMethodType]] = [
    ["local"],
    ["cache-search"],
    ["spotify"],
    ["lrclib", "musixmatch-spotify"],
    ["lrclib-search", "musixmatch"],
    ["netease", "qqmusic"],
]


def create_fetchers(cache: CacheEngine) -> dict[FetcherMethodType, BaseFetcher]:
    """Instantiate all fetchers. Returns a dict keyed by source name."""
    fetchers: dict[FetcherMethodType, BaseFetcher] = {
        "local": LocalFetcher(),
        "cache-search": CacheSearchFetcher(cache),
        "spotify": SpotifyFetcher(),
        "lrclib": LrclibFetcher(),
        "musixmatch-spotify": MusixmatchSpotifyFetcher(),
        "lrclib-search": LrclibSearchFetcher(),
        "netease": NeteaseFetcher(),
        "qqmusic": QQMusicFetcher(),
        "musixmatch": MusixmatchFetcher(),
    }
    return fetchers


def build_plan(
    fetchers: dict[FetcherMethodType, BaseFetcher],
    track: TrackMeta,
    force_method: Optional[FetcherMethodType] = None,
) -> list[list[BaseFetcher]]:
    """Return the fetch plan as a list of groups (each group runs in parallel)."""
    if force_method:
        if force_method not in fetchers:
            logger.error(f"Unknown method: {force_method}")
            return []
        return [[fetchers[force_method]]]

    plan: list[list[BaseFetcher]] = []
    for group_methods in _FETCHER_GROUPS:
        group = [
            fetchers[m]
            for m in group_methods
            if m in fetchers and fetchers[m].is_available(track)
        ]
        if group:
            plan.append(group)

    logger.debug(f"Fetch plan: {[[f.source_name for f in g] for g in plan]}")
    return plan
