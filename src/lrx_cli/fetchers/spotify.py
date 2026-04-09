"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 10:43:21
Description: Spotify fetcher — obtains synced lyrics via Spotify's internal color-lyrics API.
"""

import httpx
from loguru import logger

from .base import BaseFetcher, FetchResult
from ..authenticators.spotify import SpotifyAuthenticator, SPOTIFY_BASE_HEADERS
from ..models import TrackMeta, LyricResult, CacheStatus
from ..lrc import LRCData
from ..config import GeneralConfig, TTL_NOT_FOUND

_SPOTIFY_LYRICS_URL = "https://spclient.wg.spotify.com/color-lyrics/v2/track/"


class SpotifyFetcher(BaseFetcher):
    def __init__(self, general: GeneralConfig, auth: SpotifyAuthenticator) -> None:
        super().__init__(general, auth)

    _auth: SpotifyAuthenticator

    @property
    def source_name(self) -> str:
        return "spotify"

    def is_available(self, track: TrackMeta) -> bool:
        return bool(track.trackid) and self._auth.is_configured()

    @staticmethod
    def _format_lrc_line(start_ms: int, words: str) -> str:
        minutes = start_ms // 60000
        seconds = (start_ms // 1000) % 60
        centiseconds = round((start_ms % 1000) / 10.0)
        return f"[{minutes:02d}:{seconds:02d}.{centiseconds:02.0f}]{words}"

    @staticmethod
    def _is_truly_synced(lines: list[dict]) -> bool:
        for line in lines:
            try:
                ms = int(line.get("startTimeMs", "0"))
                if ms > 0:
                    return True
            except (ValueError, TypeError):
                continue
        return False

    async def fetch(self, track: TrackMeta, bypass_cache: bool = False) -> FetchResult:
        if not track.trackid:
            logger.debug("Spotify: skipped — no trackid in metadata")
            return FetchResult()

        logger.info(f"Spotify: fetching lyrics for trackid={track.trackid}")

        token = await self._auth.authenticate()
        if not token:
            logger.error("Spotify: cannot fetch lyrics without a token")
            return FetchResult.from_network_error()

        url = f"{_SPOTIFY_LYRICS_URL}{track.trackid}?format=json&vocalRemoval=false&market=from_token"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            **SPOTIFY_BASE_HEADERS,
        }

        try:
            async with httpx.AsyncClient(timeout=self._general.http_timeout) as client:
                res = await client.get(url, headers=headers)

                if res.status_code == 404:
                    logger.debug(f"Spotify: 404 for trackid={track.trackid}")
                    return FetchResult.from_not_found()

                if res.status_code != 200:
                    logger.error(f"Spotify: lyrics API returned {res.status_code}")
                    return FetchResult.from_network_error()

                data = res.json()

            if not isinstance(data, dict) or "lyrics" not in data:
                logger.error("Spotify: unexpected lyrics response structure")
                return FetchResult.from_network_error()

            lyrics_data = data["lyrics"]
            sync_type = lyrics_data.get("syncType", "")
            lines = lyrics_data.get("lines", [])

            if not isinstance(lines, list) or len(lines) == 0:
                logger.debug("Spotify: response contained no lyric lines")
                return FetchResult.from_not_found()

            is_synced = sync_type == "LINE_SYNCED" and self._is_truly_synced(lines)

            lrc_lines: list[str] = []
            for line in lines:
                words = line.get("words", "")
                if not isinstance(words, str):
                    continue
                try:
                    ms = int(line.get("startTimeMs", "0"))
                except (ValueError, TypeError):
                    ms = 0

                if is_synced:
                    lrc_lines.append(self._format_lrc_line(ms, words))
                else:
                    lrc_lines.append(f"[00:00.00]{words}")

            content = LRCData("\n".join(lrc_lines))
            status = (
                CacheStatus.SUCCESS_SYNCED
                if is_synced
                else CacheStatus.SUCCESS_UNSYNCED
            )

            logger.info(f"Spotify: got {status.value} lyrics ({len(lrc_lines)} lines)")
            not_found = LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)
            if is_synced:
                return FetchResult(
                    synced=LyricResult(
                        status=CacheStatus.SUCCESS_SYNCED,
                        lyrics=content,
                        source=self.source_name,
                    ),
                    unsynced=not_found,
                )
            return FetchResult(
                synced=not_found,
                unsynced=LyricResult(
                    status=CacheStatus.SUCCESS_UNSYNCED,
                    lyrics=content,
                    source=self.source_name,
                ),
            )

        except Exception as e:
            logger.error(f"Spotify: lyrics fetch failed: {e}")
            return FetchResult.from_network_error()
