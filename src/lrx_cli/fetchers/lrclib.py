"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 05:23:38
Description: LRCLIB fetcher — queries lrclib.net for synced/plain lyrics.
             Requires complete track metadata (artist, title, album, duration).
"""

from __future__ import annotations

import httpx
from loguru import logger
from urllib.parse import urlencode

from .base import BaseFetcher, FetchResult
from ..models import TrackMeta, LyricResult, CacheStatus
from ..lrc import LRCData
from ..config import (
    TTL_UNSYNCED,
    TTL_NOT_FOUND,
    UA_LRX,
)

_LRCLIB_API_URL = "https://lrclib.net/api/get"


def _parse_lrclib_response(data: dict) -> FetchResult:
    """Parse LRCLIB JSON response into synced/unsynced fetch result."""
    synced = data.get("syncedLyrics")
    unsynced = data.get("plainLyrics")

    res_synced: LyricResult = LyricResult(
        status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND
    )
    res_unsynced: LyricResult = LyricResult(
        status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND
    )

    if isinstance(synced, str) and synced.strip():
        lyrics = LRCData(synced)
        res_synced = LyricResult(
            status=CacheStatus.SUCCESS_SYNCED,
            lyrics=lyrics,
            source="lrclib",
        )

    if isinstance(unsynced, str) and unsynced.strip():
        lyrics = LRCData(unsynced)
        res_unsynced = LyricResult(
            status=CacheStatus.SUCCESS_UNSYNCED,
            lyrics=lyrics,
            source="lrclib",
            ttl=TTL_UNSYNCED,
        )

    return FetchResult(synced=res_synced, unsynced=res_unsynced)


class LrclibFetcher(BaseFetcher):
    @property
    def source_name(self) -> str:
        return "lrclib"

    def is_available(self, track: TrackMeta) -> bool:
        return track.is_complete

    async def _api_get(
        self,
        client: httpx.AsyncClient,
        track: TrackMeta,
    ) -> httpx.Response:
        """Issue one LRCLIB get request using the same path as production fetch."""
        params = {
            "track_name": track.title,
            "artist_name": track.artist,
            "album_name": track.album,
            "duration": track.length / 1000.0 if track.length else 0,
        }
        url = f"{_LRCLIB_API_URL}?{urlencode(params)}"
        return await client.get(url, headers={"User-Agent": UA_LRX})

    async def fetch(self, track: TrackMeta, bypass_cache: bool = False) -> FetchResult:
        """Fetch lyrics from LRCLIB. Requires complete metadata."""
        if not track.is_complete:
            logger.debug("LRCLIB: skipped — incomplete metadata")
            return FetchResult()

        logger.info(f"LRCLIB: fetching lyrics for {track.display_name()}")

        try:
            async with httpx.AsyncClient(timeout=self._general.http_timeout) as client:
                resp = await self._api_get(client, track)

            if resp.status_code == 404:
                logger.debug(f"LRCLIB: not found for {track.display_name()}")
                return FetchResult.from_not_found()

            if resp.status_code != 200:
                logger.error(f"LRCLIB: API returned {resp.status_code}")
                return FetchResult.from_network_error()

            data = resp.json()
            if not isinstance(data, dict):
                logger.error(f"LRCLIB: unexpected response type: {type(data).__name__}")
                return FetchResult.from_network_error()
            result = _parse_lrclib_response(data)
            if result.synced and result.synced.lyrics:
                logger.info(
                    f"LRCLIB: got synced lyrics ({len(result.synced.lyrics)} lines)"
                )
            if result.unsynced and result.unsynced.lyrics:
                logger.info(
                    f"LRCLIB: got unsynced lyrics ({len(result.unsynced.lyrics)} lines)"
                )
            return result

        except httpx.HTTPError as e:
            logger.error(f"LRCLIB: HTTP error: {e}")
            return FetchResult.from_network_error()
        except Exception as e:
            logger.error(f"LRCLIB: unexpected error: {e}")
            return FetchResult()
