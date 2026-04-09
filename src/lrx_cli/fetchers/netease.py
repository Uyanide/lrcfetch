"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 11:04:51
Description: Netease Cloud Music fetcher.

             Uses the public cloudsearch API for searching and the song/lyric API for
             retrieving lyrics. No authentication required.
"""

import asyncio
import httpx
from loguru import logger

from .base import BaseFetcher, FetchResult
from .selection import SearchCandidate, select_ranked
from ..models import TrackMeta, LyricResult, CacheStatus
from ..lrc import LRCData
from ..config import (
    TTL_NOT_FOUND,
    MULTI_CANDIDATE_DELAY_S,
    UA_BROWSER,
)

_NETEASE_SEARCH_URL = "https://music.163.com/api/cloudsearch/pc"
_NETEASE_LYRIC_URL = "https://interface3.music.163.com/api/song/lyric"
_NETEASE_BASE_HEADERS = {
    "User-Agent": UA_BROWSER,
    "Referer": "https://music.163.com/",
    "Origin": "https://music.163.com",
}


class NeteaseFetcher(BaseFetcher):
    @property
    def source_name(self) -> str:
        return "netease"

    def is_available(self, track: TrackMeta) -> bool:
        return bool(track.title)

    async def _search(
        self, track: TrackMeta, limit: int = 10
    ) -> list[tuple[int, float]]:
        query = f"{track.artist or ''} {track.title or ''}".strip()
        if not query:
            return []

        logger.debug(f"Netease: searching for '{query}' (limit={limit})")

        try:
            async with httpx.AsyncClient(timeout=self._general.http_timeout) as client:
                resp = await client.post(
                    _NETEASE_SEARCH_URL,
                    headers=_NETEASE_BASE_HEADERS,
                    data={"s": query, "type": "1", "limit": str(limit), "offset": "0"},
                )
                resp.raise_for_status()
                result = resp.json()

            if not isinstance(result, dict):
                logger.error(
                    f"Netease: search returned non-dict: {type(result).__name__}"
                )
                return []

            result_body = result.get("result")
            if not isinstance(result_body, dict):
                logger.debug("Netease: search 'result' field missing or invalid")
                return []

            songs = result_body.get("songs")
            if not isinstance(songs, list) or len(songs) == 0:
                logger.debug("Netease: search returned 0 results")
                return []

            logger.debug(f"Netease: search returned {len(songs)} candidates")

            candidates = [
                SearchCandidate(
                    item=song_id,
                    duration_ms=float(song["dt"])
                    if isinstance(song.get("dt"), int)
                    else None,
                    title=song.get("name"),
                    artist=", ".join(a.get("name", "") for a in song.get("ar", []))
                    or None,
                    album=(song.get("al") or {}).get("name"),
                )
                for song in songs
                if isinstance(song, dict) and isinstance(song_id := song.get("id"), int)
            ]
            ranked = select_ranked(
                candidates,
                track.length,
                title=track.title,
                artist=track.artist,
                album=track.album,
            )
            if ranked:
                logger.debug(
                    "Netease: top candidates: "
                    + ", ".join(f"id={i} ({c:.0f})" for i, c in ranked)
                )
            else:
                logger.debug("Netease: no suitable candidate found")
            return ranked

        except Exception as e:
            logger.error(f"Netease: search failed: {e}")
            return []

    async def _get_lyric(self, song_id: int, confidence: float = 0.0) -> FetchResult:
        logger.debug(f"Netease: fetching lyrics for song_id={song_id}")

        try:
            async with httpx.AsyncClient(timeout=self._general.http_timeout) as client:
                resp = await client.post(
                    _NETEASE_LYRIC_URL,
                    headers=_NETEASE_BASE_HEADERS,
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
                return FetchResult.from_network_error()

            lrc_obj = data.get("lrc")
            if not isinstance(lrc_obj, dict):
                logger.debug(
                    f"Netease: no 'lrc' object in response for song_id={song_id}"
                )
                return FetchResult.from_not_found()

            lrc: str = lrc_obj.get("lyric", "")
            if not isinstance(lrc, str) or not lrc.strip():
                logger.debug(f"Netease: empty lyrics for song_id={song_id}")
                return FetchResult.from_not_found()

            lrcdata = LRCData(lrc)
            status = lrcdata.detect_sync_status()
            logger.info(
                f"Netease: got {status.value} lyrics for song_id={song_id} "
                f"({len(lrcdata)} lines)"
            )
            not_found = LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)
            if status == CacheStatus.SUCCESS_SYNCED:
                return FetchResult(
                    synced=LyricResult(
                        status=CacheStatus.SUCCESS_SYNCED,
                        lyrics=lrcdata,
                        source=self.source_name,
                        confidence=confidence,
                    ),
                    unsynced=not_found,
                )
            return FetchResult(
                synced=not_found,
                unsynced=LyricResult(
                    status=CacheStatus.SUCCESS_UNSYNCED,
                    lyrics=lrcdata,
                    source=self.source_name,
                    confidence=confidence,
                ),
            )

        except Exception as e:
            logger.error(f"Netease: lyric fetch failed for song_id={song_id}: {e}")
            return FetchResult.from_network_error()

    async def fetch(self, track: TrackMeta, bypass_cache: bool = False) -> FetchResult:
        query = f"{track.artist or ''} {track.title or ''}".strip()
        if not query:
            logger.debug("Netease: skipped — insufficient metadata")
            return FetchResult()

        logger.info(f"Netease: fetching lyrics for {track.display_name()}")
        candidates = await self._search(track)
        if not candidates:
            logger.debug(f"Netease: no match found for {track.display_name()}")
            return FetchResult.from_not_found()

        res_synced: LyricResult = LyricResult(
            status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND
        )
        res_unsynced: LyricResult = LyricResult(
            status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND
        )

        for i, (song_id, confidence) in enumerate(candidates):
            if i > 0:
                await asyncio.sleep(MULTI_CANDIDATE_DELAY_S)
            result = await self._get_lyric(song_id, confidence=confidence)
            if result.synced and result.synced.status == CacheStatus.NETWORK_ERROR:
                return result
            if result.unsynced and result.unsynced.status == CacheStatus.NETWORK_ERROR:
                return result

            if (
                res_synced.status == CacheStatus.NOT_FOUND
                and result.synced
                and result.synced.status == CacheStatus.SUCCESS_SYNCED
            ):
                res_synced = result.synced
            if (
                res_unsynced.status == CacheStatus.NOT_FOUND
                and result.unsynced
                and result.unsynced.status == CacheStatus.SUCCESS_UNSYNCED
            ):
                res_unsynced = result.unsynced

            # Netease API is quite expensive, so we stop after finding synced lyrics,
            # instead of trying to find both synced and unsynced versions
            if (
                res_synced.status == CacheStatus.SUCCESS_SYNCED
                # and res_unsynced.status == CacheStatus.SUCCESS_UNSYNCED
            ):
                break

        return FetchResult(synced=res_synced, unsynced=res_unsynced)
