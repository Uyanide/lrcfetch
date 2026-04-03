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
from .selection import SearchCandidate, select_best
from ..models import TrackMeta, LyricResult, CacheStatus
from ..lrc import LRCData
from ..config import (
    HTTP_TIMEOUT,
    TTL_NOT_FOUND,
    TTL_NETWORK_ERROR,
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

    async def _search(
        self, track: TrackMeta, limit: int = 10
    ) -> tuple[Optional[int], float]:
        query = f"{track.artist or ''} {track.title or ''}".strip()
        if not query:
            return None, 0.0

        logger.debug(f"Netease: searching for '{query}' (limit={limit})")

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.post(
                    NETEASE_SEARCH_URL,
                    headers=_HEADERS,
                    data={"s": query, "type": "1", "limit": str(limit), "offset": "0"},
                )
                resp.raise_for_status()
                result = resp.json()

            if not isinstance(result, dict):
                logger.error(
                    f"Netease: search returned non-dict: {type(result).__name__}"
                )
                return None, 0.0

            result_body = result.get("result")
            if not isinstance(result_body, dict):
                logger.debug("Netease: search 'result' field missing or invalid")
                return None, 0.0

            songs = result_body.get("songs")
            if not isinstance(songs, list) or len(songs) == 0:
                logger.debug("Netease: search returned 0 results")
                return None, 0.0

            logger.debug(f"Netease: search returned {len(songs)} candidates")

            candidates = [
                SearchCandidate(
                    item=song.get("id"),
                    duration_ms=float(song["dt"])
                    if isinstance(song.get("dt"), int)
                    else None,
                    title=song.get("name"),
                    artist=", ".join(a.get("name", "") for a in song.get("ar", []))
                    or None,
                    album=(song.get("al") or {}).get("name"),
                )
                for song in songs
                if isinstance(song, dict) and song.get("id") is not None
            ]
            best_id, confidence = select_best(
                candidates,
                track.length,
                title=track.title,
                artist=track.artist,
                album=track.album,
            )
            if best_id is not None:
                logger.debug(
                    f"Netease: selected id={best_id} (confidence={confidence:.0f})"
                )
                return best_id, confidence

            logger.debug("Netease: no suitable candidate found")
            return None, 0.0

        except Exception as e:
            logger.error(f"Netease: search failed: {e}")
            return None, 0.0

    async def _get_lyric(
        self, song_id: int, confidence: float = 0.0
    ) -> Optional[LyricResult]:
        logger.debug(f"Netease: fetching lyrics for song_id={song_id}")

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.post(
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

            lrcdata = LRCData(lrc)
            status = lrcdata.detect_sync_status()
            logger.info(
                f"Netease: got {status.value} lyrics for song_id={song_id} "
                f"({len(lrcdata)} lines)"
            )
            return LyricResult(
                status=status,
                lyrics=lrcdata,
                source=self.source_name,
                confidence=confidence,
            )

        except Exception as e:
            logger.error(f"Netease: lyric fetch failed for song_id={song_id}: {e}")
            return LyricResult(status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR)

    async def fetch(
        self, track: TrackMeta, bypass_cache: bool = False
    ) -> Optional[LyricResult]:
        query = f"{track.artist or ''} {track.title or ''}".strip()
        if not query:
            logger.debug("Netease: skipped — insufficient metadata")
            return None

        logger.info(f"Netease: fetching lyrics for {track.display_name()}")
        song_id, confidence = await self._search(track)
        if not song_id:
            logger.debug(f"Netease: no match found for {track.display_name()}")
            return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

        return await self._get_lyric(song_id, confidence=confidence)
