"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 05:30:50
Description: LRCLIB search fetcher — fuzzy search via lrclib.net /api/search
"""

"""
Used when metadata is incomplete (no album or duration) but title is available.
Selects the best match by duration when track length is known.
"""

import httpx
from typing import Optional
from loguru import logger
from urllib.parse import urlencode

from .base import BaseFetcher
from ..models import TrackMeta, LyricResult, CacheStatus
from ..lrc import LRCData
from ..config import (
    HTTP_TIMEOUT,
    TTL_UNSYNCED,
    TTL_NOT_FOUND,
    TTL_NETWORK_ERROR,
    DURATION_TOLERANCE_MS,
    LRCLIB_SEARCH_URL,
    UA_LRX,
)


class LrclibSearchFetcher(BaseFetcher):
    @property
    def source_name(self) -> str:
        return "lrclib-search"

    def is_available(self, track: TrackMeta) -> bool:
        return bool(track.title)

    def fetch(
        self, track: TrackMeta, bypass_cache: bool = False
    ) -> Optional[LyricResult]:
        """Search LRCLIB for lyrics. Requires at least a title."""
        if not track.title:
            logger.debug("LRCLIB-search: skipped — no title")
            return None

        params: dict[str, str] = {"track_name": track.title}
        if track.artist:
            params["artist_name"] = track.artist
        if track.album:
            params["album_name"] = track.album

        url = f"{LRCLIB_SEARCH_URL}?{urlencode(params)}"
        logger.info(f"LRCLIB-search: searching for {track.display_name()}")

        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                resp = client.get(url, headers={"User-Agent": UA_LRX})

            if resp.status_code != 200:
                logger.error(f"LRCLIB-search: API returned {resp.status_code}")
                return LyricResult(
                    status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR
                )

            data = resp.json()

            if not isinstance(data, list) or len(data) == 0:
                logger.debug(f"LRCLIB-search: no results for {track.display_name()}")
                return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

            logger.debug(f"LRCLIB-search: got {len(data)} candidates")

            # Select best match by duration
            best = self._select_best(data, track)
            if best is None:
                logger.debug("LRCLIB-search: no valid candidate found")
                return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

            # Extract lyrics
            synced = best.get("syncedLyrics")
            unsynced = best.get("plainLyrics")

            if isinstance(synced, str) and synced.strip():
                lyrics = LRCData(synced)
                logger.info(f"LRCLIB-search: got synced lyrics ({len(lyrics)} lines)")
                return LyricResult(
                    status=CacheStatus.SUCCESS_SYNCED,
                    lyrics=lyrics,
                    source=self.source_name,
                )
            elif isinstance(unsynced, str) and unsynced.strip():
                lyrics = LRCData(unsynced)
                logger.info(f"LRCLIB-search: got unsynced lyrics ({len(lyrics)} lines)")
                return LyricResult(
                    status=CacheStatus.SUCCESS_UNSYNCED,
                    lyrics=lyrics,
                    source=self.source_name,
                    ttl=TTL_UNSYNCED,
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

    @staticmethod
    def _select_best(candidates: list[dict], track: TrackMeta) -> Optional[dict]:
        """Pick the best candidate, preferring synced lyrics and closest duration."""
        if track.length is not None:
            track_s = track.length / 1000.0
            best: Optional[dict] = None
            best_diff = float("inf")

            for item in candidates:
                if not isinstance(item, dict):
                    continue
                duration = item.get("duration")
                if not isinstance(duration, (int, float)):
                    continue
                diff = abs(duration - track_s) * 1000  # compare in ms
                if diff > DURATION_TOLERANCE_MS:
                    continue
                # Prefer synced over unsynced at similar duration
                has_synced = (
                    isinstance(item.get("syncedLyrics"), str)
                    and item["syncedLyrics"].strip()
                )
                best_synced = (
                    best is not None
                    and isinstance(best.get("syncedLyrics"), str)
                    and best["syncedLyrics"].strip()
                )
                if diff < best_diff or (
                    diff == best_diff and has_synced and not best_synced
                ):
                    best_diff = diff
                    best = item

            if best is not None:
                logger.debug(
                    f"LRCLIB-search: selected id={best.get('id')} (diff={best_diff:.0f}ms)"
                )
                return best

            logger.debug(
                f"LRCLIB-search: no candidate within {DURATION_TOLERANCE_MS}ms"
            )
            return None

        # No duration — pick first with synced lyrics, or just first
        for item in candidates:
            if (
                isinstance(item, dict)
                and isinstance(item.get("syncedLyrics"), str)
                and item["syncedLyrics"].strip()
            ):
                return item
        return candidates[0] if isinstance(candidates[0], dict) else None
