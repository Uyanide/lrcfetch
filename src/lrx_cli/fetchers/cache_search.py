"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-28 05:57:46
Description: Cache-search fetcher — cross-album fuzzy lookup in the local cache.

             Searches existing cache entries by artist + title with fuzzy normalization,
             ignoring album and source. Useful when the same track appears on different
             albums or is played from different players.
"""

from typing import Optional
from loguru import logger


from .base import BaseFetcher, FetchResult
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

    def _get_exact(self, track: TrackMeta, synced: bool) -> Optional[LyricResult]:
        exact = self._cache.find_best_positive(
            track,
            CacheStatus.SUCCESS_SYNCED if synced else CacheStatus.SUCCESS_UNSYNCED,
        )
        if exact and exact.lyrics is not None:
            logger.info(
                f"Cache-search: exact {'synced' if synced else 'unsynced'} hit ({exact.status.value})"
            )
            return exact
        return None

    def _get_fuzzy(
        self, matches: list, track: TrackMeta, synced: bool
    ) -> Optional[LyricResult]:
        filtered = [
            SearchCandidate(
                item=m,
                duration_ms=float(m["length"]) if m.get("length") else None,
                is_synced=synced,
                title=m.get("title"),
                artist=m.get("artist"),
                album=m.get("album"),
            )
            for m in matches
            if m.get("lyrics")
            and (synced and m.get("status") == CacheStatus.SUCCESS_SYNCED.value)
            or (not synced and m.get("status") == CacheStatus.SUCCESS_UNSYNCED.value)
        ]

        best, confidence = select_best(
            filtered,
            track.length,
            title=track.title,
            artist=track.artist,
            album=track.album,
        )
        if best and best.get("lyrics") is not None:
            status = (
                CacheStatus.SUCCESS_SYNCED if synced else CacheStatus.SUCCESS_UNSYNCED
            )
            logger.info(
                f"Cache-search: fuzzy {'synced' if synced else 'unsynced'} hit from "
                f"[{best.get('source')}] album={best.get('album')!r} (confidence={confidence:.0f})"
            )
            return LyricResult(
                status=status,
                lyrics=LRCData(best["lyrics"]),
                source=self.source_name,
                confidence=confidence,
            )
        return None

    async def fetch(self, track: TrackMeta, bypass_cache: bool = False) -> FetchResult:
        if bypass_cache:
            logger.debug("Cache-search: bypassed by caller")
            return FetchResult()

        if not track.title:
            logger.debug("Cache-search: skipped — no title")
            return FetchResult()

        res_synced: Optional[LyricResult] = None
        res_unsynced: Optional[LyricResult] = None

        # Fast path: exact metadata match (artist+title+album), single SQL query
        res_synced = self._get_exact(track, synced=True)
        res_unsynced = self._get_exact(track, synced=False)
        if res_synced and res_unsynced:
            return FetchResult(synced=res_synced, unsynced=res_unsynced)

        # Slow path: fuzzy cross-album search
        matches = self._cache.search_by_meta(title=track.title, length=track.length)

        if not matches:
            logger.debug(f"Cache-search: no match for {track.display_name()}")
            return FetchResult(synced=res_synced, unsynced=res_unsynced)

        if not res_synced:
            res_synced = self._get_fuzzy(matches, track, synced=True)
        if not res_unsynced:
            res_unsynced = self._get_fuzzy(matches, track, synced=False)

        return FetchResult(synced=res_synced, unsynced=res_unsynced)
