"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 11:09:53
Description: Core orchestrator — coordinates fetchers with cache-aware fallback
"""

import asyncio
from typing import Optional
from loguru import logger

from .fetchers import FetcherMethodType, build_plan, create_fetchers
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


def _normalize_result(result: LyricResult) -> LyricResult:
    """Normalize unsynced lyrics before returning."""
    if result.status == CacheStatus.SUCCESS_UNSYNCED and result.lyrics:
        return LyricResult(
            status=result.status,
            lyrics=result.lyrics.normalize_unsynced(),
            source=result.source,
            ttl=result.ttl,
            confidence=result.confidence,
        )
    return result


class LrcManager:
    """Main entry point for fetching lyrics with caching."""

    def __init__(self, db_path: str) -> None:
        self.cache = CacheEngine(db_path=db_path)
        self.fetchers = create_fetchers(self.cache)

    async def _run_group(
        self,
        group: list[BaseFetcher],
        track: TrackMeta,
        bypass_cache: bool,
    ) -> list[tuple[str, LyricResult]]:
        """Run one group: cache-check first, then parallel-fetch uncached. Returns (source, result) pairs."""
        cached_results: list[tuple[str, LyricResult]] = []
        need_fetch: list[BaseFetcher] = []

        for fetcher in group:
            source = fetcher.source_name
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
                    is_trusted = cached.confidence >= HIGH_CONFIDENCE
                    logger.info(
                        f"[{source}] cache hit: {cached.status.value}"
                        f" (confidence={cached.confidence:.0f})"
                    )
                    cached_results.append((source, cached))
                    # Return immediately on trusted synced cache hit
                    if cached.status == CacheStatus.SUCCESS_SYNCED and is_trusted:
                        return cached_results
                    continue
            elif not fetcher.self_cached:
                logger.debug(f"[{source}] cache bypassed")
            need_fetch.append(fetcher)

        if need_fetch:
            task_map: dict[asyncio.Task, BaseFetcher] = {
                asyncio.create_task(f.fetch(track, bypass_cache=bypass_cache)): f
                for f in need_fetch
            }
            pending = set(task_map)

            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                found_trusted = False
                for task in done:
                    fetcher = task_map[task]
                    source = fetcher.source_name
                    try:
                        result = task.result()
                    except Exception as e:
                        logger.error(f"[{source}] fetch raised: {e}")
                        continue

                    if result is None:
                        logger.debug(f"[{source}] returned None")
                        continue

                    if not fetcher.self_cached and not bypass_cache:
                        ttl = result.ttl or _STATUS_TTL.get(
                            result.status, TTL_NOT_FOUND
                        )
                        self.cache.set(track, source, result, ttl_seconds=ttl)

                    if result.status in (
                        CacheStatus.SUCCESS_SYNCED,
                        CacheStatus.SUCCESS_UNSYNCED,
                    ):
                        logger.info(
                            f"[{source}] got {result.status.value} lyrics"
                            f" (confidence={result.confidence:.0f})"
                        )
                    cached_results.append((source, result))

                    if (
                        result.status == CacheStatus.SUCCESS_SYNCED
                        and result.confidence >= HIGH_CONFIDENCE
                    ):
                        found_trusted = True

                if found_trusted:
                    for t in pending:
                        t.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    break

        return cached_results

    async def _fetch_for_track(
        self,
        track: TrackMeta,
        force_method: Optional[FetcherMethodType],
        bypass_cache: bool,
    ) -> Optional[LyricResult]:
        track = enrich_track(track)
        logger.info(f"Fetching lyrics for: {track.display_name()}")

        plan = build_plan(self.fetchers, track, force_method)
        if not plan:
            return None

        best_result: Optional[LyricResult] = None

        for group in plan:
            group_results = await self._run_group(group, track, bypass_cache)

            for source, result in group_results:
                if result.status not in (
                    CacheStatus.SUCCESS_SYNCED,
                    CacheStatus.SUCCESS_UNSYNCED,
                ):
                    continue

                is_trusted = result.confidence >= HIGH_CONFIDENCE

                # Trusted synced → return immediately
                if result.status == CacheStatus.SUCCESS_SYNCED and is_trusted:
                    logger.info(
                        f"Returning {result.status.value} lyrics from {source}"
                        f" (confidence={result.confidence:.0f})"
                    )
                    return _normalize_result(result)

                if best_result is None or _is_better(result, best_result):
                    best_result = result

        if best_result:
            logger.info(
                f"Returning {best_result.status.value} lyrics from {best_result.source}"
            )
            return _normalize_result(best_result)

        logger.info(f"No lyrics found for {track.display_name()}")
        return None

    def fetch_for_track(
        self,
        track: TrackMeta,
        force_method: Optional[FetcherMethodType] = None,
        bypass_cache: bool = False,
    ) -> Optional[LyricResult]:
        """Fetch lyrics for *track* using the group-based parallel pipeline."""
        return asyncio.run(self._fetch_for_track(track, force_method, bypass_cache))

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
