"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 11:09:53
Description: Core orchestrator — coordinates fetchers with cache-aware fallback
"""

"""
Fetch pipeline:
  1. Check cache for each source in the fallback sequence
  2. For sources without a valid cache hit, call the fetcher
  3. Cache every result (success, not-found, or error) per source
  4. Return the best result by confidence (highest wins)
"""

from typing import Optional
from loguru import logger

from .fetchers import FetcherMethodType, create_fetchers
from .fetchers.base import BaseFetcher
from .cache import CacheEngine
from .lrc import LRCData
from .config import (
    TTL_SYNCED,
    TTL_UNSYNCED,
    TTL_NOT_FOUND,
    TTL_NETWORK_ERROR,
    HIGH_CONFIDENCE,
)
from .models import TrackMeta, LyricResult, CacheStatus
from .enrichers import enrich_track


# Maps CacheStatus to the default TTL used when storing results
_STATUS_TTL: dict[CacheStatus, Optional[int]] = {
    CacheStatus.SUCCESS_SYNCED: TTL_SYNCED,
    CacheStatus.SUCCESS_UNSYNCED: TTL_UNSYNCED,
    CacheStatus.NOT_FOUND: TTL_NOT_FOUND,
    CacheStatus.NETWORK_ERROR: TTL_NETWORK_ERROR,
}


def _is_better(new: LyricResult, old: LyricResult) -> bool:
    """Compare two results: higher confidence wins; synced breaks ties."""
    if new.confidence != old.confidence:
        return new.confidence > old.confidence
    # Equal confidence — prefer synced as tiebreaker
    return (
        new.status == CacheStatus.SUCCESS_SYNCED
        and old.status != CacheStatus.SUCCESS_SYNCED
    )


class LrcManager:
    """Main entry point for fetching lyrics with caching."""

    def __init__(self, db_path: str) -> None:
        self.cache = CacheEngine(db_path=db_path)
        self.fetchers = create_fetchers(self.cache)

    def _build_sequence(
        self, track: TrackMeta, force_method: Optional[FetcherMethodType] = None
    ) -> list[BaseFetcher]:
        """Determine the ordered list of fetchers to try."""
        if force_method:
            if force_method not in self.fetchers:
                logger.error(f"Unknown method: {force_method}")
                return []
            return [self.fetchers[force_method]]

        sequence: list[BaseFetcher] = []
        for method in self.fetchers.keys():
            if self.fetchers[method].is_available(track):
                sequence.append(self.fetchers[method])

        logger.debug(f"Fallback sequence: {[f.source_name for f in sequence]}")
        return sequence

    def fetch_for_track(
        self,
        track: TrackMeta,
        force_method: Optional[FetcherMethodType] = None,
        bypass_cache: bool = False,
    ) -> Optional[LyricResult]:
        """Fetch lyrics for *track* using the fallback pipeline.

        Each source is checked against the cache independently:
        - Cache hit with synced lyrics → return immediately
        - Cache hit with negative status (NOT_FOUND / NETWORK_ERROR) → skip source
        - Cache miss or unsynced → call fetcher, then cache the result

        After all sources are tried, returns the best result found
        (highest confidence wins).
        """
        track = enrich_track(track)
        logger.info(f"Fetching lyrics for: {track.display_name()}")

        sequence = self._build_sequence(track, force_method)
        if not sequence:
            return None

        # Best result seen so far (highest confidence wins)
        best_result: Optional[LyricResult] = None

        for fetcher in sequence:
            source = fetcher.source_name

            # Cache check (skip for fetchers that handle their own caching)
            if not bypass_cache and not fetcher.self_cached:
                cached = self.cache.get(track, source)
                if cached:
                    if cached.status in (
                        CacheStatus.NOT_FOUND,
                        CacheStatus.NETWORK_ERROR,
                    ):
                        logger.debug(
                            f"[{source}] cache hit: {cached.status.value}, skipping"
                        )
                        continue

                    # Positive cache hit — apply the same confidence evaluation
                    # as fresh fetches so that low-confidence cached results
                    # don't block better results from later fetchers.
                    is_trusted = cached.confidence >= HIGH_CONFIDENCE
                    logger.info(
                        f"[{source}] cache hit: {cached.status.value}"
                        f" (confidence={cached.confidence:.0f})"
                    )
                    if cached.status == CacheStatus.SUCCESS_SYNCED and is_trusted:
                        return cached
                    if best_result is None or _is_better(cached, best_result):
                        best_result = cached
                    continue
            elif not fetcher.self_cached:
                logger.debug(f"[{source}] cache bypassed")

            # Fetch
            logger.debug(f"[{source}] calling fetcher...")
            result = fetcher.fetch(track, bypass_cache=bypass_cache)

            if not result:
                logger.debug(f"[{source}] returned None (no result)")
                continue

            # Cache the result (skip for self-cached fetchers)
            if not fetcher.self_cached and not bypass_cache:
                ttl = result.ttl or _STATUS_TTL.get(result.status, TTL_NOT_FOUND)
                self.cache.set(track, source, result, ttl_seconds=ttl)

            # Evaluate result
            if result.status in (
                CacheStatus.SUCCESS_SYNCED,
                CacheStatus.SUCCESS_UNSYNCED,
            ):
                is_trusted = result.confidence >= HIGH_CONFIDENCE
                logger.info(
                    f"[{source}] got {result.status.value} lyrics"
                    f" (confidence={result.confidence:.0f})"
                )
                # Trusted synced → return immediately
                if result.status == CacheStatus.SUCCESS_SYNCED and is_trusted:
                    return result
                # Track best result by confidence
                if best_result is None or _is_better(result, best_result):
                    best_result = result

            # NOT_FOUND / NETWORK_ERROR: already cached, try next

        # Return best available
        if best_result:
            if (
                best_result.status == CacheStatus.SUCCESS_UNSYNCED
                and best_result.lyrics
            ):
                best_result = LyricResult(
                    status=best_result.status,
                    lyrics=best_result.lyrics.normalize_unsynced(),
                    source=best_result.source,
                    ttl=best_result.ttl,
                    confidence=best_result.confidence,
                )
            logger.info(
                f"Returning {best_result.status.value} lyrics from {best_result.source}"
            )
        else:
            logger.info(f"No lyrics found for {track.display_name()}")

        return best_result

    def manual_insert(
        self,
        track: TrackMeta,
        lyrics: str,
    ) -> None:
        """Manually insert lyrics into the cache for a track."""
        track = enrich_track(track)
        logger.info(f"Manually inserting lyrics for: {track.display_name()}")
        lrc = LRCData(lyrics)
        result = LyricResult(
            status=lrc.detect_sync_status(),
            lyrics=lrc,
            source="manual",
            ttl=None,
        )
        self.cache.set(track, "manual", result, ttl_seconds=None)
        logger.info("Lyrics inserted into cache.")
