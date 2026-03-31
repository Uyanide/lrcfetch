"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-31 01:54:02
Description: QQ Music fetcher via self-hosted API proxy
"""

"""
Requires a running qq-music-api instance.
The base URL is read from the QQ_MUSIC_API_URL environment variable.

Search → pick best match by duration → fetch LRC lyrics.
"""

from typing import Optional
import httpx
from loguru import logger

from .base import BaseFetcher
from ..models import TrackMeta, LyricResult, CacheStatus
from ..lrc import detect_sync_status, normalize_tags
from ..config import (
    HTTP_TIMEOUT,
    TTL_NOT_FOUND,
    TTL_NETWORK_ERROR,
    DURATION_TOLERANCE_MS,
    QQ_MUSIC_API_URL,
)


class QQMusicFetcher(BaseFetcher):
    @property
    def source_name(self) -> str:
        return "qqmusic"

    def _search(self, track: TrackMeta, limit: int = 10) -> Optional[str]:
        """Search QQ Music and return the best-matching song MID."""
        query = f"{track.artist or ''} {track.title or ''}".strip()
        if not query:
            return None

        logger.debug(f"QQMusic: searching for '{query}' (limit={limit})")

        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                resp = client.get(
                    f"{QQ_MUSIC_API_URL}/api/search",
                    params={"keyword": query, "type": "song", "num": limit},
                )
                resp.raise_for_status()
                data = resp.json()

            if data.get("code") != 0:
                logger.error(f"QQMusic: search API error: {data}")
                return None

            songs = data.get("data", {}).get("list", [])
            if not songs:
                logger.debug("QQMusic: search returned 0 results")
                return None

            logger.debug(f"QQMusic: search returned {len(songs)} candidates")

            # Duration-based best-match selection
            if track.length is not None:
                track_ms = track.length
                best_mid: Optional[str] = None
                best_diff = float("inf")

                for song in songs:
                    if not isinstance(song, dict):
                        continue
                    mid = song.get("mid")
                    name = song.get("name", "?")
                    # interval is in seconds
                    interval = song.get("interval")
                    if not isinstance(interval, int):
                        logger.debug(
                            f"  candidate {mid} '{name}': no duration, skipped"
                        )
                        continue
                    duration_ms = interval * 1000
                    diff = abs(duration_ms - track_ms)
                    logger.debug(
                        f"  candidate {mid} '{name}': "
                        f"duration={duration_ms}ms, diff={diff}ms"
                    )
                    if diff < best_diff:
                        best_diff = diff
                        best_mid = mid

                if best_mid is not None and best_diff <= DURATION_TOLERANCE_MS:
                    logger.debug(
                        f"QQMusic: selected mid={best_mid} (diff={best_diff}ms)"
                    )
                    return best_mid

                logger.debug(
                    f"QQMusic: no candidate within {DURATION_TOLERANCE_MS}ms "
                    f"(best diff={best_diff}ms)"
                )
                return None

            # No duration info — take the first result
            first = songs[0]
            if not isinstance(first, dict) or "mid" not in first:
                logger.error("QQMusic: first search result has no 'mid'")
                return None
            logger.debug(
                f"QQMusic: no duration available, using first result "
                f"mid={first['mid']} '{first.get('name', '?')}'"
            )
            return first["mid"]

        except Exception as e:
            logger.error(f"QQMusic: search failed: {e}")
            return None

    def _get_lyric(self, mid: str) -> Optional[LyricResult]:
        """Fetch lyrics for a given QQ Music song MID."""
        logger.debug(f"QQMusic: fetching lyrics for mid={mid}")

        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                resp = client.get(
                    f"{QQ_MUSIC_API_URL}/api/lyric",
                    params={"mid": mid},
                )
                resp.raise_for_status()
                data = resp.json()

            if data.get("code") != 0:
                logger.error(f"QQMusic: lyric API error: {data}")
                return LyricResult(
                    status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR
                )

            lrc = data.get("data", {}).get("lyric", "")
            if not isinstance(lrc, str) or not lrc.strip():
                logger.debug(f"QQMusic: empty lyrics for mid={mid}")
                return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

            lrc = normalize_tags(lrc)
            status = detect_sync_status(lrc)
            logger.info(
                f"QQMusic: got {status.value} lyrics for mid={mid} "
                f"({len(lrc.splitlines())} lines)"
            )
            return LyricResult(
                status=status, lyrics=lrc.strip(), source=self.source_name
            )

        except Exception as e:
            logger.error(f"QQMusic: lyric fetch failed for mid={mid}: {e}")
            return LyricResult(status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR)

    def fetch(
        self, track: TrackMeta, bypass_cache: bool = False
    ) -> Optional[LyricResult]:
        """Search for the track and fetch its lyrics."""
        if not QQ_MUSIC_API_URL:
            logger.debug("QQMusic: skipped — QQ_MUSIC_API_URL not configured")
            return None

        query = f"{track.artist or ''} {track.title or ''}".strip()
        if not query:
            logger.debug("QQMusic: skipped — insufficient metadata")
            return None

        logger.info(f"QQMusic: fetching lyrics for {track.display_name()}")
        mid = self._search(track)
        if not mid:
            logger.debug(f"QQMusic: no match found for {track.display_name()}")
            return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

        return self._get_lyric(mid)
