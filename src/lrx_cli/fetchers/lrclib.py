"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 05:23:38
Description: LRCLIB fetcher — queries lrclib.net for synced/plain lyrics.
             Requires complete track metadata (artist, title, album, duration).
"""

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


class LrclibFetcher(BaseFetcher):
    @property
    def source_name(self) -> str:
        return "lrclib"

    def is_available(self, track: TrackMeta) -> bool:
        return track.is_complete

    async def fetch(self, track: TrackMeta, bypass_cache: bool = False) -> FetchResult:
        """Fetch lyrics from LRCLIB. Requires complete metadata."""
        if not track.is_complete:
            logger.debug("LRCLIB: skipped — incomplete metadata")
            return FetchResult()

        params = {
            "track_name": track.title,
            "artist_name": track.artist,
            "album_name": track.album,
            "duration": track.length / 1000.0 if track.length else 0,
        }
        url = f"{_LRCLIB_API_URL}?{urlencode(params)}"
        logger.info(f"LRCLIB: fetching lyrics for {track.display_name()}")

        try:
            async with httpx.AsyncClient(timeout=self._general.http_timeout) as client:
                resp = await client.get(url, headers={"User-Agent": UA_LRX})

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
                logger.info(f"LRCLIB: got synced lyrics ({len(lyrics)} lines)")
                res_synced = LyricResult(
                    status=CacheStatus.SUCCESS_SYNCED,
                    lyrics=lyrics,
                    source=self.source_name,
                )

            if isinstance(unsynced, str) and unsynced.strip():
                lyrics = LRCData(unsynced)
                logger.info(f"LRCLIB: got unsynced lyrics ({len(lyrics)} lines)")
                res_unsynced = LyricResult(
                    status=CacheStatus.SUCCESS_UNSYNCED,
                    lyrics=lyrics,
                    source=self.source_name,
                    ttl=TTL_UNSYNCED,
                )

            return FetchResult(synced=res_synced, unsynced=res_unsynced)

        except httpx.HTTPError as e:
            logger.error(f"LRCLIB: HTTP error: {e}")
            return FetchResult.from_network_error()
        except Exception as e:
            logger.error(f"LRCLIB: unexpected error: {e}")
            return FetchResult()
