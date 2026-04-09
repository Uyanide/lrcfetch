"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-31 01:54:02
Description: QQ Music fetcher via self-hosted API proxy.

             Requires a running qq-music-api instance.
             The base URL is read from the QQ_MUSIC_API_URL environment variable.

             Search → pick best match → fetch LRC lyrics.
"""

import asyncio
import httpx
from loguru import logger

from .base import BaseFetcher, FetchResult
from .selection import SearchCandidate, select_ranked
from ..models import TrackMeta, LyricResult, CacheStatus
from ..lrc import LRCData
from ..config import (
    GeneralConfig,
    TTL_NOT_FOUND,
    MULTI_CANDIDATE_DELAY_S,
)

_QQ_MUSIC_API_SEARCH_ENDPOINT = "/api/search"
_QQ_MUSIC_API_LYRIC_ENDPOINT = "/api/lyric"
from ..authenticators import QQMusicAuthenticator


class QQMusicFetcher(BaseFetcher):
    _auth: QQMusicAuthenticator

    def __init__(self, general: GeneralConfig, auth: QQMusicAuthenticator) -> None:
        super().__init__(general, auth)

    @property
    def source_name(self) -> str:
        return "qqmusic"

    def is_available(self, track: TrackMeta) -> bool:
        return bool(track.title) and self._auth.is_configured()

    async def _search(
        self, track: TrackMeta, limit: int = 10
    ) -> list[tuple[str, float]]:
        query = f"{track.artist or ''} {track.title or ''}".strip()
        if not query:
            return []

        logger.debug(f"QQMusic: searching for '{query}' (limit={limit})")

        try:
            async with httpx.AsyncClient(timeout=self._general.http_timeout) as client:
                resp = await client.get(
                    f"{await self._auth.authenticate()}{_QQ_MUSIC_API_SEARCH_ENDPOINT}",
                    params={"keyword": query, "type": "song", "num": limit},
                )
                resp.raise_for_status()
                data = resp.json()

            if data.get("code") != 0:
                logger.error(f"QQMusic: search API error: {data}")
                return []

            songs = data.get("data", {}).get("list", [])
            if not songs:
                logger.debug("QQMusic: search returned 0 results")
                return []

            logger.debug(f"QQMusic: search returned {len(songs)} candidates")

            candidates = [
                SearchCandidate(
                    item=mid,
                    duration_ms=float(song["interval"]) * 1000
                    if isinstance(song.get("interval"), int)
                    else None,
                    title=song.get("name"),
                    artist=", ".join(s.get("name", "") for s in song.get("singer", []))
                    or None,
                    album=(song.get("album") or {}).get("name"),
                )
                for song in songs
                if isinstance(song, dict) and isinstance(mid := song.get("mid"), str)
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
                    "QQMusic: top candidates: "
                    + ", ".join(f"mid={m} ({c:.0f})" for m, c in ranked)
                )
            else:
                logger.debug("QQMusic: no suitable candidate found")
            return ranked

        except Exception as e:
            logger.error(f"QQMusic: search failed: {e}")
            return []

    async def _get_lyric(self, mid: str, confidence: float = 0.0) -> FetchResult:
        logger.debug(f"QQMusic: fetching lyrics for mid={mid}")

        try:
            async with httpx.AsyncClient(timeout=self._general.http_timeout) as client:
                resp = await client.get(
                    f"{await self._auth.authenticate()}{_QQ_MUSIC_API_LYRIC_ENDPOINT}",
                    params={"mid": mid},
                )
                resp.raise_for_status()
                data = resp.json()

            if data.get("code") != 0:
                logger.error(f"QQMusic: lyric API error: {data}")
                return FetchResult.from_network_error()

            lrc = data.get("data", {}).get("lyric", "")
            if not isinstance(lrc, str) or not lrc.strip():
                logger.debug(f"QQMusic: empty lyrics for mid={mid}")
                return FetchResult.from_not_found()

            lrcdata = LRCData(lrc)
            status = lrcdata.detect_sync_status()
            logger.info(
                f"QQMusic: got {status.value} lyrics for mid={mid} ({len(lrcdata)} lines)"
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
            logger.error(f"QQMusic: lyric fetch failed for mid={mid}: {e}")
            return FetchResult.from_network_error()

    async def fetch(self, track: TrackMeta, bypass_cache: bool = False) -> FetchResult:
        if not self._auth.is_configured():
            logger.debug("QQMusic: skipped — Auth not configured")
            return FetchResult()

        query = f"{track.artist or ''} {track.title or ''}".strip()
        if not query:
            logger.debug("QQMusic: skipped — insufficient metadata")
            return FetchResult()

        logger.info(f"QQMusic: fetching lyrics for {track.display_name()}")
        candidates = await self._search(track)
        if not candidates:
            logger.debug(f"QQMusic: no match found for {track.display_name()}")
            return FetchResult.from_not_found()

        res_synced: LyricResult = LyricResult(
            status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND
        )
        res_unsynced: LyricResult = LyricResult(
            status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND
        )

        for i, (mid, confidence) in enumerate(candidates):
            if i > 0:
                await asyncio.sleep(MULTI_CANDIDATE_DELAY_S)
            result = await self._get_lyric(mid, confidence=confidence)
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

            # QQMusic API is quite expensive, so we stop after finding synced lyrics,
            # instead of trying to find both synced and unsynced versions
            if (
                res_synced.status == CacheStatus.SUCCESS_SYNCED
                # and res_unsynced.status == CacheStatus.SUCCESS_UNSYNCED
            ):
                break

        return FetchResult(synced=res_synced, unsynced=res_unsynced)
