"""Core orchestrator — coordinates fetchers with cache-aware fallback.

Fetch pipeline:
  1. Check cache for each source in the fallback sequence
  2. For sources without a valid cache hit, call the fetcher
  3. Cache every result (success, not-found, or error) per source
  4. Return the best result (synced > unsynced > None)
"""

import re
from typing import Optional
from loguru import logger
from lrcfetch.models import TrackMeta, LyricResult, CacheStatus
from lrcfetch.config import TTL_SYNCED, TTL_UNSYNCED, TTL_NOT_FOUND, TTL_NETWORK_ERROR
from lrcfetch.cache import CacheEngine
from lrcfetch.fetchers.base import BaseFetcher
from lrcfetch.fetchers.local import LocalFetcher
from lrcfetch.fetchers.spotify import SpotifyFetcher
from lrcfetch.fetchers.lrclib import LrclibFetcher
from lrcfetch.fetchers.lrclib_search import LrclibSearchFetcher
from lrcfetch.fetchers.netease import NeteaseFetcher

# Matches any LRC time tag at the start of a line: [mm:ss.cc] or [mm:ss.ccc]
_LRC_LINE_RE = re.compile(r"^\[(\d{2}:\d{2}\.\d{2,3})\]", re.MULTILINE)


def _normalize_unsynced(lyrics: str) -> str:
    """Normalize unsynced lyrics so every line has a [00:00.00] tag.

    - Lines that already have time tags: replace with [00:00.00]
    - Lines without time tags: prepend [00:00.00]
    - Blank lines are kept as-is
    """
    out: list[str] = []
    for line in lyrics.splitlines():
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        # Strip existing time tag(s) from the beginning
        cleaned = _LRC_LINE_RE.sub("", stripped)
        # Could have multiple tags like [00:12.34][00:56.78]text
        while _LRC_LINE_RE.match(cleaned):
            cleaned = _LRC_LINE_RE.sub("", cleaned)
        out.append(f"[00:00.00]{cleaned}")
    return "\n".join(out)


# Maps CacheStatus to the default TTL used when storing results
_STATUS_TTL: dict[CacheStatus, int] = {
    CacheStatus.SUCCESS_SYNCED: TTL_SYNCED,
    CacheStatus.SUCCESS_UNSYNCED: TTL_UNSYNCED,
    CacheStatus.NOT_FOUND: TTL_NOT_FOUND,
    CacheStatus.NETWORK_ERROR: TTL_NETWORK_ERROR,
}


class LrcManager:
    """Main entry point for fetching lyrics with caching."""

    def __init__(self) -> None:
        self.cache = CacheEngine()
        self.fetchers: dict[str, BaseFetcher] = {
            "local": LocalFetcher(),
            "spotify": SpotifyFetcher(),
            "lrclib": LrclibFetcher(),
            "lrclib-search": LrclibSearchFetcher(),
            "netease": NeteaseFetcher(),
        }

    def _build_sequence(
        self, track: TrackMeta, force_method: Optional[str] = None
    ) -> list[BaseFetcher]:
        """Determine the ordered list of fetchers to try."""
        if force_method:
            if force_method not in self.fetchers:
                logger.error(f"Unknown method: {force_method}")
                return []
            return [self.fetchers[force_method]]

        sequence: list[BaseFetcher] = []
        if track.is_local:
            sequence.append(self.fetchers["local"])
        if track.trackid:
            sequence.append(self.fetchers["spotify"])
        if track.is_complete:
            sequence.append(self.fetchers["lrclib"])
        if track.title:
            sequence.append(self.fetchers["lrclib-search"])
        sequence.append(self.fetchers["netease"])

        logger.debug(
            f"Fallback sequence: {[f.source_name for f in sequence]}"
        )
        return sequence

    def fetch_for_track(
        self,
        track: TrackMeta,
        force_method: Optional[str] = None,
        bypass_cache: bool = False,
    ) -> Optional[LyricResult]:
        """Fetch lyrics for *track* using the fallback pipeline.

        Each source is checked against the cache independently:
        - Cache hit with synced lyrics → return immediately
        - Cache hit with negative status (NOT_FOUND / NETWORK_ERROR) → skip source
        - Cache miss or unsynced → call fetcher, then cache the result

        After all sources are tried, returns the best result found
        (synced > unsynced > None).
        """
        logger.info(f"Fetching lyrics for: {track.display_name()}")

        sequence = self._build_sequence(track, force_method)
        if not sequence:
            return None

        # Best result seen so far (synced wins over unsynced)
        best_result: Optional[LyricResult] = None

        for fetcher in sequence:
            source = fetcher.source_name

            # Cache check
            if not bypass_cache:
                cached = self.cache.get(track, source)
                if cached:
                    if cached.status == CacheStatus.SUCCESS_SYNCED:
                        logger.info(f"[{source}] cache hit: synced lyrics")
                        return cached
                    elif cached.status == CacheStatus.SUCCESS_UNSYNCED:
                        logger.debug(f"[{source}] cache hit: unsynced lyrics (continuing)")
                        if best_result is None:
                            best_result = cached
                        continue  # Try next source for synced
                    elif cached.status in (CacheStatus.NOT_FOUND, CacheStatus.NETWORK_ERROR):
                        logger.debug(f"[{source}] cache hit: {cached.status.value}, skipping")
                        continue
            else:
                logger.debug(f"[{source}] cache bypassed")

            # Fetch
            logger.debug(f"[{source}] calling fetcher...")
            result = fetcher.fetch(track)

            if not result:
                logger.debug(f"[{source}] returned None (no result)")
                continue

            # Cache the result
            ttl = result.ttl or _STATUS_TTL.get(result.status, TTL_NOT_FOUND)
            self.cache.set(track, source, result, ttl_seconds=ttl)

            # Evaluate result
            if result.status == CacheStatus.SUCCESS_SYNCED:
                logger.info(f"[{source}] got synced lyrics")
                return result

            if result.status == CacheStatus.SUCCESS_UNSYNCED:
                logger.debug(f"[{source}] got unsynced lyrics (continuing)")
                if best_result is None:
                    best_result = result

            # NOT_FOUND / NETWORK_ERROR: already cached, try next

        # Return best available
        if best_result:
            # Normalize unsynced lyrics: set all timestamps to [00:00.00]
            if (
                best_result.status == CacheStatus.SUCCESS_UNSYNCED
                and best_result.lyrics
            ):
                best_result = LyricResult(
                    status=best_result.status,
                    lyrics=_normalize_unsynced(best_result.lyrics),
                    source=best_result.source,
                    ttl=best_result.ttl,
                )
            logger.info(
                f"Returning unsynced lyrics from {best_result.source} "
                f"(no synced source found)"
            )
        else:
            logger.info(f"No lyrics found for {track.display_name()}")

        return best_result
