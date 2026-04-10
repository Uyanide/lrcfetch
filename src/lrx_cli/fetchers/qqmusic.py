"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-31 01:54:02
Description: QQ Music fetcher via self-hosted API proxy.

             Requires a running qq-music-api instance.
             The base URL is read from the QQ_MUSIC_API_URL environment variable.

             Search → pick best match → fetch LRC lyrics.
"""

import asyncio
from loguru import logger

from .base import BaseFetcher, FetchResult
from .selection import SearchCandidate, select_ranked
from ..authenticators import QQMusicAuthenticator
from ..models import TrackMeta, LyricResult, CacheStatus
from ..lrc import LRCData
from ..config import (
    GeneralConfig,
    TTL_NOT_FOUND,
    MULTI_CANDIDATE_DELAY_S,
)


def _parse_qq_search(data: dict) -> list[SearchCandidate[str]]:
    """Parse QQMusic search response into normalized candidates."""
    if data.get("code") != 0:
        return []

    songs = data.get("data", {}).get("list", [])
    if not isinstance(songs, list):
        return []

    return [
        SearchCandidate(
            item=mid,
            duration_ms=float(song["interval"]) * 1000
            if isinstance(song.get("interval"), int)
            else None,
            title=song.get("name"),
            artist=", ".join(s.get("name", "") for s in song.get("singer", [])) or None,
            album=(song.get("album") or {}).get("name"),
        )
        for song in songs
        if isinstance(song, dict) and isinstance(mid := song.get("mid"), str)
    ]


def _parse_qq_lyrics(data: dict) -> LRCData | None:
    """Parse QQMusic lyric response to LRCData."""
    if data.get("code") != 0:
        return None

    lrc = data.get("data", {}).get("lyric", "")
    if not isinstance(lrc, str) or not lrc.strip():
        return None
    return LRCData(lrc)


class QQMusicFetcher(BaseFetcher):
    _auth: QQMusicAuthenticator

    def __init__(self, general: GeneralConfig, auth: QQMusicAuthenticator) -> None:
        super().__init__(general, auth)

    @property
    def source_name(self) -> str:
        return "qqmusic"

    def is_available(self, track: TrackMeta) -> bool:
        return bool(track.title) and self._auth.is_configured()

    async def _api_search(
        self,
        track: TrackMeta,
        limit: int,
    ) -> dict | None:
        """Return raw QQMusic search payload for one track."""
        query = f"{track.artist or ''} {track.title or ''}".strip()
        if not query:
            return None
        data = await self._auth.search(query, limit)
        if not isinstance(data, dict):
            return None
        return data

    async def _api_lyric(
        self,
        mid: str,
    ) -> dict | None:
        """Return raw QQMusic lyric payload for one song MID."""
        data = await self._auth.get_lyric(mid)
        if not isinstance(data, dict):
            return None
        return data

    async def _api_lyric_track(
        self,
        track: TrackMeta,
        limit: int,
    ) -> dict | None:
        """Return raw QQMusic lyric payload for top-ranked search candidate."""
        search_data = await self._api_search(track, limit)
        if search_data is None:
            return None

        candidates = _parse_qq_search(search_data)
        if not candidates:
            return None

        ranked = select_ranked(
            candidates,
            track.length,
            title=track.title,
            artist=track.artist,
            album=track.album,
        )
        if not ranked:
            return None

        mid = ranked[0][0]
        return await self._api_lyric(mid)

    async def _search(
        self, track: TrackMeta, limit: int = 10
    ) -> list[tuple[str, float]]:
        search_data = await self._api_search(track, limit)
        if search_data is None:
            return []

        query = f"{track.artist or ''} {track.title or ''}".strip()
        logger.debug(f"QQMusic: searching for '{query}' (limit={limit})")

        candidates = _parse_qq_search(search_data)
        if not candidates:
            logger.debug("QQMusic: search returned 0 results")
            return []

        logger.debug(f"QQMusic: search returned {len(candidates)} candidates")
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

    async def _get_lyric(self, mid: str, confidence: float = 0.0) -> FetchResult:
        logger.debug(f"QQMusic: fetching lyrics for mid={mid}")
        data = await self._api_lyric(mid)
        if data is None:
            return FetchResult.from_network_error()

        lrcdata = _parse_qq_lyrics(data)
        if lrcdata is None:
            logger.debug(f"QQMusic: empty lyrics for mid={mid}")
            return FetchResult.from_not_found()

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
