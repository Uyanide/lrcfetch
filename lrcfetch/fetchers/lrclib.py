"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 05:23:38
Description: LRCLIB fetcher — queries lrclib.net for synced/plain lyrics
"""

"""
Requires complete track metadata (artist, title, album, duration).
"""

from typing import Optional
import httpx
from loguru import logger
from urllib.parse import urlencode

from .base import BaseFetcher
from ..models import TrackMeta, LyricResult, CacheStatus
from ..config import (
    HTTP_TIMEOUT,
    TTL_UNSYNCED,
    TTL_NOT_FOUND,
    TTL_NETWORK_ERROR,
    LRCLIB_API_URL,
    UA_LRCFETCH,
)


class LrclibFetcher(BaseFetcher):
    @property
    def source_name(self) -> str:
        return "lrclib"

    def fetch(
        self, track: TrackMeta, bypass_cache: bool = False
    ) -> Optional[LyricResult]:
        """Fetch lyrics from LRCLIB. Requires complete metadata."""
        if not track.is_complete:
            logger.debug("LRCLIB: skipped — incomplete metadata")
            return None

        params = {
            "track_name": track.title,
            "artist_name": track.artist,
            "album_name": track.album,
            "duration": track.length / 1000.0 if track.length else 0,
        }

        url = f"{LRCLIB_API_URL}?{urlencode(params)}"
        logger.info(f"LRCLIB: fetching lyrics for {track.display_name()}")

        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                resp = client.get(url, headers={"User-Agent": UA_LRCFETCH})

            if resp.status_code == 404:
                logger.debug(f"LRCLIB: not found for {track.display_name()}")
                return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

            if resp.status_code != 200:
                logger.error(f"LRCLIB: API returned {resp.status_code}")
                return LyricResult(
                    status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR
                )

            data = resp.json()

            # Validate response
            if not isinstance(data, dict):
                logger.error(f"LRCLIB: unexpected response type: {type(data).__name__}")
                return LyricResult(
                    status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR
                )

            synced = data.get("syncedLyrics")
            unsynced = data.get("plainLyrics")

            if isinstance(synced, str) and synced.strip():
                logger.info(
                    f"LRCLIB: got synced lyrics ({len(synced.splitlines())} lines)"
                )
                return LyricResult(
                    status=CacheStatus.SUCCESS_SYNCED,
                    lyrics=synced.strip(),
                    source=self.source_name,
                )
            elif isinstance(unsynced, str) and unsynced.strip():
                logger.info(
                    f"LRCLIB: got unsynced lyrics ({len(unsynced.splitlines())} lines)"
                )
                return LyricResult(
                    status=CacheStatus.SUCCESS_UNSYNCED,
                    lyrics=unsynced.strip(),
                    source=self.source_name,
                    ttl=TTL_UNSYNCED,
                )
            else:
                logger.debug(f"LRCLIB: empty response for {track.display_name()}")
                return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

        except httpx.HTTPError as e:
            logger.error(f"LRCLIB: HTTP error: {e}")
            return LyricResult(status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR)
        except Exception as e:
            logger.error(f"LRCLIB: unexpected error: {e}")
            return None
