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
from .selection import SearchCandidate, select_best
from ..models import TrackMeta, LyricResult, CacheStatus
from ..cache import CacheEngine
from ..lrc import LRCData


class CacheSearchFetcher(BaseFetcher):
    def __init__(self, cache: CacheEngine) -> None:
        self._cache = cache

    @property
    def source_name(self) -> str:
        return "cache-search"

    @property
    def self_cached(self) -> bool:
        return True

    def is_available(self, track: TrackMeta) -> bool:
        return bool(track.title)

    async def fetch(
        self, track: TrackMeta, bypass_cache: bool = False
    ) -> Optional[LyricResult]:
        if bypass_cache:
            logger.debug("Cache-search: bypassed by caller")
            return None

        if not track.title:
            logger.debug("Cache-search: skipped — no title")
            return None

        # Fast path: exact metadata match (artist+title+album), single SQL query
        exact = self._cache.find_best_positive(track)
        if exact:
            logger.info(f"Cache-search: exact hit ({exact.status.value})")
            return exact

        # Slow path: fuzzy cross-album search
        matches = self._cache.search_by_meta(
            artist=track.artist,
            title=track.title,
            length=track.length,
        )

        if not matches:
            logger.debug(f"Cache-search: no match for {track.display_name()}")
            return None

        # Pick best by confidence scoring
        candidates = [
            SearchCandidate(
                item=m,
                duration_ms=float(m["length"]) if m.get("length") else None,
                is_synced=m.get("status") == CacheStatus.SUCCESS_SYNCED.value,
                title=m.get("title"),
                artist=m.get("artist"),
                album=m.get("album"),
            )
            for m in matches
            if m.get("lyrics")
        ]
        best, confidence = select_best(
            candidates,
            track.length,
            title=track.title,
            artist=track.artist,
            album=track.album,
        )

        if not best:
            return None

        status = CacheStatus(best["status"])
        logger.info(
            f"Cache-search: fuzzy hit from [{best.get('source')}] "
            f"album={best.get('album')!r} ({status.value}, confidence={confidence:.0f})"
        )
        return LyricResult(
            status=status,
            lyrics=LRCData(best["lyrics"]),
            source=self.source_name,
            confidence=confidence,
        )
