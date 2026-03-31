"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 11:04:51
Description: Netease Cloud Music fetcher
"""

"""
Uses the public cloudsearch API for searching and the song/lyric API for
retrieving lyrics. No authentication required.

Search results are filtered by duration when the track has a known length
to avoid returning lyrics for the wrong version of a song.
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
    NETEASE_SEARCH_URL,
    NETEASE_LYRIC_URL,
    UA_BROWSER,
)

_HEADERS = {
    "User-Agent": UA_BROWSER,
    "Referer": "https://music.163.com/",
}


class NeteaseFetcher(BaseFetcher):
    @property
    def source_name(self) -> str:
        return "netease"

    def is_available(self, track: TrackMeta) -> bool:
        return bool(track.title)

    def _search(self, track: TrackMeta, limit: int = 10) -> Optional[int]:
        """Search Netease and return the best-matching song ID.

        When ``track.length`` is available, candidates are ranked by duration
        difference and only accepted if within ``DURATION_TOLERANCE_MS``.
        """
        query = f"{track.artist or ''} {track.title or ''}".strip()
        if not query:
            return None

        logger.debug(f"Netease: searching for '{query}' (limit={limit})")

        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                resp = client.post(
                    NETEASE_SEARCH_URL,
                    headers=_HEADERS,
                    data={"s": query, "type": "1", "limit": str(limit), "offset": "0"},
                )
                resp.raise_for_status()
                result = resp.json()

            # Validate response
            if not isinstance(result, dict):
                logger.error(
                    f"Netease: search returned non-dict: {type(result).__name__}"
                )
                return None

            result_body = result.get("result")
            if not isinstance(result_body, dict):
                logger.debug("Netease: search 'result' field missing or invalid")
                return None

            songs = result_body.get("songs")
            if not isinstance(songs, list) or len(songs) == 0:
                logger.debug("Netease: search returned 0 results")
                return None

            logger.debug(f"Netease: search returned {len(songs)} candidates")

            # Duration-based best-match selection
            if track.length is not None:
                track_ms = track.length
                best_id: Optional[int] = None
                best_diff = float("inf")

                for song in songs:
                    if not isinstance(song, dict):
                        continue
                    sid = song.get("id")
                    name = song.get("name", "?")
                    duration = song.get("dt")  # milliseconds
                    if not isinstance(duration, int):
                        logger.debug(
                            f"  candidate {sid} '{name}': no duration, skipped"
                        )
                        continue
                    diff = abs(duration - track_ms)
                    logger.debug(
                        f"  candidate {sid} '{name}': "
                        f"duration={duration}ms, diff={diff}ms"
                    )
                    if diff < best_diff:
                        best_diff = diff
                        best_id = sid

                if best_id is not None and best_diff <= DURATION_TOLERANCE_MS:
                    logger.debug(f"Netease: selected id={best_id} (diff={best_diff}ms)")
                    return best_id

                logger.debug(
                    f"Netease: no candidate within {DURATION_TOLERANCE_MS}ms "
                    f"(best diff={best_diff}ms)"
                )
                return None

            # No duration info — take the first result
            first = songs[0]
            if not isinstance(first, dict) or "id" not in first:
                logger.error("Netease: first search result has no 'id'")
                return None
            logger.debug(
                f"Netease: no duration available, using first result "
                f"id={first['id']} '{first.get('name', '?')}'"
            )
            return first["id"]

        except Exception as e:
            logger.error(f"Netease: search failed: {e}")
            return None

    def _get_lyric(self, song_id: int) -> Optional[LyricResult]:
        """Fetch lyrics for a given Netease song ID."""
        logger.debug(f"Netease: fetching lyrics for song_id={song_id}")

        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                resp = client.post(
                    NETEASE_LYRIC_URL,
                    headers=_HEADERS,
                    data={
                        "id": str(song_id),
                        "cp": "false",
                        "tv": "0",
                        "lv": "0",
                        "rv": "0",
                        "kv": "0",
                        "yv": "0",
                        "ytv": "0",
                        "yrv": "0",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            # Validate response
            if not isinstance(data, dict):
                logger.error(
                    f"Netease: lyric response is not dict: {type(data).__name__}"
                )
                return LyricResult(
                    status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR
                )

            lrc_obj = data.get("lrc")
            if not isinstance(lrc_obj, dict):
                logger.debug(
                    f"Netease: no 'lrc' object in response for song_id={song_id}"
                )
                return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

            lrc: str = lrc_obj.get("lyric", "")
            if not isinstance(lrc, str) or not lrc.strip():
                logger.debug(f"Netease: empty lyrics for song_id={song_id}")
                return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

            # Determine sync status
            lrc = normalize_tags(lrc)
            status = detect_sync_status(lrc)
            logger.info(
                f"Netease: got {status.value} lyrics for song_id={song_id} "
                f"({len(lrc.splitlines())} lines)"
            )
            return LyricResult(
                status=status, lyrics=lrc.strip(), source=self.source_name
            )

        except Exception as e:
            logger.error(f"Netease: lyric fetch failed for song_id={song_id}: {e}")
            return LyricResult(status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR)

    def fetch(
        self, track: TrackMeta, bypass_cache: bool = False
    ) -> Optional[LyricResult]:
        """Search for the track and fetch its lyrics."""
        query = f"{track.artist or ''} {track.title or ''}".strip()
        if not query:
            logger.debug("Netease: skipped — insufficient metadata")
            return None

        logger.info(f"Netease: fetching lyrics for {track.display_name()}")
        song_id = self._search(track)
        if not song_id:
            logger.debug(f"Netease: no match found for {track.display_name()}")
            return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

        return self._get_lyric(song_id)
