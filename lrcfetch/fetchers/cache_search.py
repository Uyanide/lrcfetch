"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-28 05:57:46
Description: Cache-search fetcher — cross-album fuzzy lookup in the local cache
"""

"""
Searches existing cache entries by artist + title with fuzzy normalization,
ignoring album and source. Useful when the same track appears on different
albums or is played from different players.
"""

from typing import Optional
from loguru import logger

from .base import BaseFetcher
from ..models import TrackMeta, LyricResult, CacheStatus
from ..cache import CacheEngine


class CacheSearchFetcher(BaseFetcher):
    def __init__(self, cache: CacheEngine) -> None:
        self._cache = cache

    @property
    def source_name(self) -> str:
        return "cache-search"

    def fetch(self, track: TrackMeta) -> Optional[LyricResult]:
        if not track.title:
            logger.debug("Cache-search: skipped — no title")
            return None

        matches = self._cache.search_by_meta(
            artist=track.artist,
            title=track.title,
            length=track.length,
        )

        if not matches:
            logger.debug(f"Cache-search: no match for {track.display_name()}")
            return None

        # Pick best: prefer synced, then first available
        best = None
        for m in matches:
            if m.get("status") == CacheStatus.SUCCESS_SYNCED.value:
                best = m
                break
            if best is None:
                best = m

        if not best or not best.get("lyrics"):
            return None

        status = CacheStatus(best["status"])
        logger.info(
            f"Cache-search: hit from [{best.get('source')}] "
            f"album={best.get('album')!r} ({status.value})"
        )
        return LyricResult(
            status=status,
            lyrics=best["lyrics"],
            source=self.source_name,
        )
