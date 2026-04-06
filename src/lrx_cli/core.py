"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 11:09:53
Description: Core orchestrator — coordinates fetchers with cache-aware fallback.
             Also handles enrichers & authenticators & …
"""

import asyncio
from typing import Optional
from loguru import logger

from .fetchers import FetcherMethodType, build_plan, create_fetchers
from .fetchers.base import BaseFetcher, FetchResult
from .authenticators import create_authenticators
from .cache import CacheEngine
from .lrc import LRCData
from .config import (
    TTL_SYNCED,
    TTL_UNSYNCED,
    TTL_NOT_FOUND,
    TTL_NETWORK_ERROR,
    HIGH_CONFIDENCE,
    SLOT_SYNCED,
    SLOT_UNSYNCED,
)
from .models import TrackMeta, LyricResult, CacheStatus
from .enrichers import create_enrichers, enrich_track
from .ranking import is_better_result, select_best_positive


# Maps CacheStatus to the default TTL used when storing results
_STATUS_TTL: dict[CacheStatus, Optional[int]] = {
    CacheStatus.SUCCESS_SYNCED: TTL_SYNCED,
    CacheStatus.SUCCESS_UNSYNCED: TTL_UNSYNCED,
    CacheStatus.NOT_FOUND: TTL_NOT_FOUND,
    CacheStatus.NETWORK_ERROR: TTL_NETWORK_ERROR,
}


def _pick_for_return(
    result: FetchResult,
    allow_unsynced: bool,
) -> Optional[LyricResult]:
    """Pick best positive slot for final selection under current strategy."""
    candidates: list[LyricResult] = []
    if result.synced and result.synced.status == CacheStatus.SUCCESS_SYNCED:
        candidates.append(result.synced)
    if (
        allow_unsynced
        and result.unsynced
        and result.unsynced.status == CacheStatus.SUCCESS_UNSYNCED
    ):
        candidates.append(result.unsynced)

    return select_best_positive(candidates, allow_unsynced=True)


def _iter_slot_results(result: FetchResult) -> list[tuple[str, LyricResult]]:
    """Return all non-None slot results with their cache slot key."""
    out: list[tuple[str, LyricResult]] = []
    if result.synced is not None:
        out.append((SLOT_SYNCED, result.synced))
    if result.unsynced is not None:
        out.append((SLOT_UNSYNCED, result.unsynced))
    return out


def _pick_cached_for_return(
    cached_rows: list[LyricResult],
    allow_unsynced: bool,
) -> Optional[LyricResult]:
    """Convert cached slot rows into FetchResult-like view and select return candidate."""
    fr = FetchResult()
    for row in cached_rows:
        if row.status == CacheStatus.SUCCESS_SYNCED:
            fr = FetchResult(synced=row, unsynced=fr.unsynced)
        elif row.status == CacheStatus.SUCCESS_UNSYNCED:
            fr = FetchResult(synced=fr.synced, unsynced=row)
    return _pick_for_return(fr, allow_unsynced)


def _has_negative_for_both_slots(cached_rows: list[LyricResult]) -> bool:
    """True when both slot rows are present and both are negative."""
    if len(cached_rows) < 2:
        return False
    return all(
        r.status in (CacheStatus.NOT_FOUND, CacheStatus.NETWORK_ERROR)
        for r in cached_rows
    )


class LrcManager:
    """Main entry point for fetching lyrics with caching."""

    def __init__(self, db_path: str) -> None:
        self.cache = CacheEngine(db_path=db_path)
        self.authenticators = create_authenticators(self.cache)
        self.fetchers = create_fetchers(self.cache, self.authenticators)
        self.enrichers = create_enrichers(self.authenticators)

    async def _run_group(
        self,
        group: list[BaseFetcher],
        track: TrackMeta,
        bypass_cache: bool,
        allow_unsynced: bool,
    ) -> list[tuple[str, LyricResult]]:
        """Run one group with slot-aware cache check then parallel fetch uncached sources."""
        cached_results: list[tuple[str, LyricResult]] = []
        need_fetch: list[BaseFetcher] = []

        for fetcher in group:
            source = fetcher.source_name
            if not bypass_cache and not fetcher.self_cached:
                cached_rows = self.cache.get_all(track, source)
                if cached_rows:
                    if _has_negative_for_both_slots(cached_rows):
                        logger.debug(
                            f"[{source}] cache hit: all slots negative, skipping"
                        )
                        continue

                    cached_for_return = _pick_cached_for_return(
                        cached_rows, allow_unsynced
                    )
                    if cached_for_return is not None:
                        is_trusted = cached_for_return.confidence >= HIGH_CONFIDENCE
                        logger.info(
                            f"[{source}] cache hit: {cached_for_return.status.value}"
                            f" (confidence={cached_for_return.confidence:.0f})"
                        )
                        cached_results.append((source, cached_for_return))
                        # Return immediately on trusted synced cache hit
                        if (
                            cached_for_return.status == CacheStatus.SUCCESS_SYNCED
                            and is_trusted
                        ):
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

                    return_result = _pick_for_return(result, allow_unsynced)

                    if not fetcher.self_cached and not bypass_cache:
                        for slot_kind, slot_result in _iter_slot_results(result):
                            ttl = slot_result.ttl or _STATUS_TTL.get(
                                slot_result.status, TTL_NOT_FOUND
                            )
                            self.cache.set(
                                track,
                                source,
                                slot_result,
                                ttl_seconds=ttl,
                                positive_kind=slot_kind,
                            )

                    if return_result is not None:
                        logger.info(
                            f"[{source}] got {return_result.status.value} lyrics"
                            f" (confidence={return_result.confidence:.0f})"
                        )
                        cached_results.append((source, return_result))

                    if (
                        return_result is not None
                        and return_result.status == CacheStatus.SUCCESS_SYNCED
                        and return_result.confidence >= HIGH_CONFIDENCE
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
        allow_unsynced: bool,
    ) -> Optional[LyricResult]:
        track = await enrich_track(track, self.enrichers)
        logger.info(f"Fetching lyrics for: {track.display_name()}")

        plan = build_plan(self.fetchers, track, force_method)
        if not plan:
            return None

        best_result: Optional[LyricResult] = None

        for group in plan:
            group_results = await self._run_group(
                group,
                track,
                bypass_cache,
                allow_unsynced,
            )

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
                    return result

                if best_result is None or is_better_result(
                    result,
                    best_result,
                    allow_unsynced=allow_unsynced,
                ):
                    best_result = result

        if best_result:
            if (
                best_result.status == CacheStatus.SUCCESS_UNSYNCED
                and not allow_unsynced
            ):
                logger.info(
                    f"Unsynced lyrics found from {best_result.source}, but unsynced results are not allowed"
                )
                return None
            logger.info(
                f"Returning {best_result.status.value} lyrics from {best_result.source}"
            )
            return best_result

        logger.info(f"No lyrics found for {track.display_name()}")
        return None

    def fetch_for_track(
        self,
        track: TrackMeta,
        force_method: Optional[FetcherMethodType] = None,
        bypass_cache: bool = False,
        allow_unsynced: bool = False,
    ) -> Optional[LyricResult]:
        """Fetch lyrics for *track* using the group-based parallel pipeline."""
        return asyncio.run(
            self._fetch_for_track(
                track,
                force_method,
                bypass_cache,
                allow_unsynced,
            )
        )

    def manual_insert(
        self,
        track: TrackMeta,
        lyrics: str,
    ) -> None:
        """Manually insert lyrics into the cache for a track."""
        track = asyncio.run(enrich_track(track, self.enrichers))
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
