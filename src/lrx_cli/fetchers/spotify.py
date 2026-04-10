"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 10:43:21
Description: Spotify fetcher — obtains synced lyrics via Spotify's internal color-lyrics API.
"""

from loguru import logger

from .base import BaseFetcher, FetchResult
from ..authenticators.spotify import SpotifyAuthenticator
from ..models import TrackMeta, LyricResult, CacheStatus
from ..lrc import LRCData
from ..config import GeneralConfig, TTL_NOT_FOUND


def _format_lrc_line(start_ms: int, words: str) -> str:
    minutes = start_ms // 60000
    seconds = (start_ms // 1000) % 60
    centiseconds = round((start_ms % 1000) / 10.0)
    return f"[{minutes:02d}:{seconds:02d}.{centiseconds:02.0f}]{words}"


def _is_truly_synced(lines: list[dict]) -> bool:
    for line in lines:
        try:
            ms = int(line.get("startTimeMs", "0"))
            if ms > 0:
                return True
        except (ValueError, TypeError):
            continue
    return False


def _parse_spotify_lyrics(data: dict) -> LRCData | None:
    """Parse Spotify color-lyrics payload to LRCData."""
    lyrics_data = data.get("lyrics")
    if not isinstance(lyrics_data, dict):
        return None

    sync_type = lyrics_data.get("syncType", "")
    lines = lyrics_data.get("lines", [])
    if not isinstance(lines, list) or len(lines) == 0:
        return None

    is_synced = sync_type == "LINE_SYNCED" and _is_truly_synced(lines)

    lrc_lines: list[str] = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        words = line.get("words", "")
        if not isinstance(words, str):
            continue
        try:
            ms = int(line.get("startTimeMs", "0"))
        except (ValueError, TypeError):
            ms = 0

        if is_synced:
            lrc_lines.append(_format_lrc_line(ms, words))
        else:
            lrc_lines.append(f"[00:00.00]{words}")

    if not lrc_lines:
        return None
    return LRCData("\n".join(lrc_lines))


class SpotifyFetcher(BaseFetcher):
    def __init__(self, general: GeneralConfig, auth: SpotifyAuthenticator) -> None:
        super().__init__(general, auth)

    _auth: SpotifyAuthenticator

    @property
    def source_name(self) -> str:
        return "spotify"

    def is_available(self, track: TrackMeta) -> bool:
        return bool(track.trackid) and self._auth.is_configured()

    async def _api_lyrics(self, track: TrackMeta) -> dict | None:
        """Return raw Spotify lyrics payload for one track using production auth path."""
        if not track.trackid:
            return None
        data = await self._auth.get_lyrics(track.trackid)
        if not isinstance(data, dict):
            return None
        return data

    async def fetch(self, track: TrackMeta, bypass_cache: bool = False) -> FetchResult:
        if not track.trackid:
            logger.debug("Spotify: skipped — no trackid in metadata")
            return FetchResult()

        logger.info(f"Spotify: fetching lyrics for trackid={track.trackid}")

        data = await self._api_lyrics(track)
        if data is None:
            logger.debug(f"Spotify: no lyrics payload for trackid={track.trackid}")
            return FetchResult.from_not_found()

        content = _parse_spotify_lyrics(data)
        if content is None:
            logger.debug("Spotify: response contained no parseable lyric lines")
            return FetchResult.from_not_found()

        status = content.detect_sync_status()
        logger.info(f"Spotify: got {status.value} lyrics ({len(content)} lines)")
        not_found = LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)
        if status == CacheStatus.SUCCESS_SYNCED:
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
