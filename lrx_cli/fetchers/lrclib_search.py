"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 05:30:50
Description: LRCLIB search fetcher — fuzzy search via lrclib.net /api/search
"""

"""
Used when metadata is incomplete (no album or duration) but title is available.
Selects the best match by duration when track length is known.
"""

import asyncio
import httpx
from typing import Optional
from loguru import logger
from urllib.parse import urlencode

from .base import BaseFetcher
from .selection import SearchCandidate, select_best
from ..models import TrackMeta, LyricResult, CacheStatus
from ..lrc import LRCData
from ..config import (
    HTTP_TIMEOUT,
    TTL_UNSYNCED,
    TTL_NOT_FOUND,
    TTL_NETWORK_ERROR,
    LRCLIB_SEARCH_URL,
    UA_LRX,
)


class LrclibSearchFetcher(BaseFetcher):
    @property
    def source_name(self) -> str:
        return "lrclib-search"

    def is_available(self, track: TrackMeta) -> bool:
        return bool(track.title)

    def _build_queries(self, track: TrackMeta) -> list[dict[str, str]]:
        """Build up to 4 query param sets, from most specific to least.

        1. title + artist + album (if all present)
        2. title + artist (if artist present)
        3. title + album (if album present)
        4. title only
        """
        assert track.title is not None
        title = track.title
        queries: list[dict[str, str]] = []

        if track.artist and track.album:
            queries.append(
                {
                    "track_name": title,
                    "artist_name": track.artist,
                    "album_name": track.album,
                }
            )
        if track.artist:
            queries.append({"track_name": title, "artist_name": track.artist})
        if track.album:
            queries.append({"track_name": title, "album_name": track.album})
        queries.append({"track_name": title})

        return queries

    async def fetch(
        self, track: TrackMeta, bypass_cache: bool = False
    ) -> Optional[LyricResult]:
        if not track.title:
            logger.debug("LRCLIB-search: skipped — no title")
            return None

        queries = self._build_queries(track)
        logger.info(f"LRCLIB-search: searching for {track.display_name()}")

        seen_ids: set[int] = set()
        candidates: list[dict] = []
        had_error = False

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:

                async def _query(params: dict[str, str]) -> tuple[list[dict], bool]:
                    url = f"{LRCLIB_SEARCH_URL}?{urlencode(params)}"
                    logger.debug(f"LRCLIB-search: query {params}")
                    try:
                        resp = await client.get(url, headers={"User-Agent": UA_LRX})
                    except httpx.HTTPError as e:
                        logger.error(f"LRCLIB-search: HTTP error: {e}")
                        return [], True
                    if resp.status_code != 200:
                        logger.error(f"LRCLIB-search: API returned {resp.status_code}")
                        return [], True
                    data = resp.json()
                    if not isinstance(data, list):
                        return [], False
                    return [item for item in data if isinstance(item, dict)], False

                all_results = await asyncio.gather(*(_query(p) for p in queries))

            for items, err in all_results:
                if err:
                    had_error = True
                for item in items:
                    item_id = item.get("id")
                    if item_id is not None and item_id in seen_ids:
                        continue
                    if item_id is not None:
                        seen_ids.add(item_id)
                    candidates.append(item)

            if not candidates:
                if had_error:
                    return LyricResult(
                        status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR
                    )
                logger.debug(f"LRCLIB-search: no results for {track.display_name()}")
                return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

            logger.debug(
                f"LRCLIB-search: got {len(candidates)} unique candidates "
                f"from {len(queries)} queries"
            )

            mapped = [
                SearchCandidate(
                    item=item,
                    duration_ms=item["duration"] * 1000
                    if isinstance(item.get("duration"), (int, float))
                    else None,
                    is_synced=isinstance(item.get("syncedLyrics"), str)
                    and bool(item["syncedLyrics"].strip()),
                    title=item.get("trackName"),
                    artist=item.get("artistName"),
                    album=item.get("albumName"),
                )
                for item in candidates
            ]
            best, confidence = select_best(
                mapped,
                track.length,
                title=track.title,
                artist=track.artist,
                album=track.album,
            )
            if best is None:
                logger.debug("LRCLIB-search: no valid candidate found")
                return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

            synced = best.get("syncedLyrics")
            unsynced = best.get("plainLyrics")

            if isinstance(synced, str) and synced.strip():
                lyrics = LRCData(synced)
                logger.info(
                    f"LRCLIB-search: got synced lyrics ({len(lyrics)} lines, confidence={confidence:.0f})"
                )
                return LyricResult(
                    status=CacheStatus.SUCCESS_SYNCED,
                    lyrics=lyrics,
                    source=self.source_name,
                    confidence=confidence,
                )
            elif isinstance(unsynced, str) and unsynced.strip():
                lyrics = LRCData(unsynced)
                logger.info(
                    f"LRCLIB-search: got unsynced lyrics ({len(lyrics)} lines, confidence={confidence:.0f})"
                )
                return LyricResult(
                    status=CacheStatus.SUCCESS_UNSYNCED,
                    lyrics=lyrics,
                    source=self.source_name,
                    ttl=TTL_UNSYNCED,
                    confidence=confidence,
                )
            else:
                logger.debug("LRCLIB-search: best candidate has empty lyrics")
                return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

        except httpx.HTTPError as e:
            logger.error(f"LRCLIB-search: HTTP error: {e}")
            return LyricResult(status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR)
        except Exception as e:
            logger.error(f"LRCLIB-search: unexpected error: {e}")
            return None
