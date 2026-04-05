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

import asyncio
from typing import Optional
import httpx
from loguru import logger

from .base import BaseFetcher
from .selection import SearchCandidate, select_ranked
from ..models import TrackMeta, LyricResult, CacheStatus
from ..lrc import LRCData
from ..config import (
    HTTP_TIMEOUT,
    TTL_NOT_FOUND,
    TTL_NETWORK_ERROR,
    MULTI_CANDIDATE_DELAY_S,
)
from ..authenticators import QQMusicAuthenticator


class QQMusicFetcher(BaseFetcher):
    def __init__(self, auth: QQMusicAuthenticator) -> None:
        self.auth = auth

    @property
    def source_name(self) -> str:
        return "qqmusic"

    def is_available(self, track: TrackMeta) -> bool:
        return bool(track.title) and self.auth.is_configured()

    async def _search(
        self, track: TrackMeta, limit: int = 10
    ) -> list[tuple[str, float]]:
        query = f"{track.artist or ''} {track.title or ''}".strip()
        if not query:
            return []

        logger.debug(f"QQMusic: searching for '{query}' (limit={limit})")

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.get(
                    f"{await self.auth.authenticate()}/api/search",
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

    async def _get_lyric(
        self, mid: str, confidence: float = 0.0
    ) -> Optional[LyricResult]:
        logger.debug(f"QQMusic: fetching lyrics for mid={mid}")

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.get(
                    f"{await self.auth.authenticate()}/api/lyric",
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

            lrcdata = LRCData(lrc)
            status = lrcdata.detect_sync_status()
            logger.info(
                f"QQMusic: got {status.value} lyrics for mid={mid} ({len(lrcdata)} lines)"
            )
            return LyricResult(
                status=status,
                lyrics=lrcdata,
                source=self.source_name,
                confidence=confidence,
            )

        except Exception as e:
            logger.error(f"QQMusic: lyric fetch failed for mid={mid}: {e}")
            return LyricResult(status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR)

    async def fetch(
        self, track: TrackMeta, bypass_cache: bool = False
    ) -> Optional[LyricResult]:
        if not self.auth.is_configured():
            logger.debug("QQMusic: skipped — Auth not configured")
            return None

        query = f"{track.artist or ''} {track.title or ''}".strip()
        if not query:
            logger.debug("QQMusic: skipped — insufficient metadata")
            return None

        logger.info(f"QQMusic: fetching lyrics for {track.display_name()}")
        candidates = await self._search(track)
        if not candidates:
            logger.debug(f"QQMusic: no match found for {track.display_name()}")
            return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

        for i, (mid, confidence) in enumerate(candidates):
            if i > 0:
                await asyncio.sleep(MULTI_CANDIDATE_DELAY_S)
            result = await self._get_lyric(mid, confidence=confidence)
            if result is None or result.status == CacheStatus.NETWORK_ERROR:
                return result
            if result.status != CacheStatus.NOT_FOUND:
                return result

        return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)
