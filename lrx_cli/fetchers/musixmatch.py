"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-04-04 15:28:34
Description: Musixmatch fetchers (desktop API, usertoken auth)
"""

"""
Uses the Musixmatch desktop API (apic-desktop.musixmatch.com).
Requires MUSIXMATCH_USERTOKEN from https://curators.musixmatch.com/settings
→ "Copy debug info" → find UserToken.

Two fetchers:
  musixmatch-spotify  — direct lookup by Spotify track ID (exact, no search)
  musixmatch          — metadata search + multi-candidate fallback
"""

import json
from typing import Optional
from urllib.parse import urlencode

import httpx
from loguru import logger

from .base import BaseFetcher
from .selection import SearchCandidate, select_best
from ..lrc import LRCData
from ..models import CacheStatus, LyricResult, TrackMeta
from ..config import (
    HTTP_TIMEOUT,
    MUSIXMATCH_MACRO_URL,
    MUSIXMATCH_SEARCH_URL,
    MUSIXMATCH_USERTOKEN,
    TTL_NETWORK_ERROR,
    TTL_NOT_FOUND,
)

_MXM_HEADERS = {"Cookie": "x-mxm-token-guid="}

_MXM_MACRO_BASE_PARAMS: dict[str, str] = {
    "format": "json",
    "namespace": "lyrics_richsynched",
    "subtitle_format": "mxm",
    "optional_calls": "track.richsync",
    "app_id": "web-desktop-app-v1.0",
}


def _format_ts(s: float) -> str:
    mm = int(s) // 60
    ss = int(s) % 60
    cs = min(round((s % 1) * 100), 99)
    return f"[{mm:02d}:{ss:02d}.{cs:02d}]"


def _parse_richsync(body: str) -> Optional[str]:
    """Parse richsync JSON body → LRC text. Each entry: {"ts": float, "x": str}."""
    try:
        data = json.loads(body)
        if not isinstance(data, list):
            return None
        lines = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            ts = entry.get("ts")
            x = entry.get("x")
            if not isinstance(ts, (int, float)) or not isinstance(x, str):
                continue
            lines.append(f"{_format_ts(float(ts))}{x}")
        return "\n".join(lines) if lines else None
    except Exception:
        return None


def _parse_subtitle(body: str) -> Optional[str]:
    """Parse subtitle JSON body → LRC text. Each entry: {"text": str, "time": {"total": float}}."""
    try:
        data = json.loads(body)
        if not isinstance(data, list):
            return None
        lines = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            text = entry.get("text")
            time_obj = entry.get("time")
            if not isinstance(text, str) or not isinstance(time_obj, dict):
                continue
            total = time_obj.get("total")
            if not isinstance(total, (int, float)):
                continue
            lines.append(f"{_format_ts(float(total))}{text}")
        return "\n".join(lines) if lines else None
    except Exception:
        return None


async def _fetch_macro(
    client: httpx.AsyncClient,
    params: dict[str, str],
) -> Optional[LRCData]:
    """
    Call macro.subtitles.get with given params merged onto base params.
    Returns LRCData on success (richsync preferred over subtitle),
    None when the API returns no usable lyrics.
    Raises on HTTP/network errors.
    """
    merged = {**_MXM_MACRO_BASE_PARAMS, **params}
    url = f"{MUSIXMATCH_MACRO_URL}?{urlencode(merged)}"
    logger.debug(f"Musixmatch: macro call with {list(params.keys())}")

    resp = await client.get(url, headers=_MXM_HEADERS)
    resp.raise_for_status()

    data = resp.json()
    # Musixmatch returns body=[] (not {}) when the track is not found
    body = data.get("message", {}).get("body", {})
    if not isinstance(body, dict):
        return None
    macro_calls = body.get("macro_calls", {})
    if not isinstance(macro_calls, dict):
        return None

    # Prefer richsync (word-level timing)
    richsync_msg = macro_calls.get("track.richsync.get", {}).get("message", {})
    if (
        isinstance(richsync_msg, dict)
        and richsync_msg.get("header", {}).get("status_code") == 200
    ):
        richsync_body = (
            richsync_msg.get("body", {}).get("richsync", {}).get("richsync_body")
        )
        if isinstance(richsync_body, str):
            lrc_text = _parse_richsync(richsync_body)
            if lrc_text:
                lrc = LRCData(lrc_text)
                if lrc:
                    logger.debug("Musixmatch: got richsync lyrics")
                    return lrc

    # Fall back to subtitle (line-level timing)
    subtitle_msg = macro_calls.get("track.subtitles.get", {}).get("message", {})
    if (
        isinstance(subtitle_msg, dict)
        and subtitle_msg.get("header", {}).get("status_code") == 200
    ):
        subtitle_list = subtitle_msg.get("body", {}).get("subtitle_list", [])
        if isinstance(subtitle_list, list) and subtitle_list:
            subtitle_body = subtitle_list[0].get("subtitle", {}).get("subtitle_body")
            if isinstance(subtitle_body, str):
                lrc_text = _parse_subtitle(subtitle_body)
                if lrc_text:
                    lrc = LRCData(lrc_text)
                    if lrc:
                        logger.debug("Musixmatch: got subtitle lyrics")
                        return lrc

    logger.debug("Musixmatch: no usable lyrics in macro response")
    return None


class MusixmatchSpotifyFetcher(BaseFetcher):
    """Direct lookup by Spotify track ID — no search, single request."""

    @property
    def source_name(self) -> str:
        return "musixmatch-spotify"

    def is_available(self, track: TrackMeta) -> bool:
        return bool(track.trackid) and bool(MUSIXMATCH_USERTOKEN)

    async def fetch(
        self, track: TrackMeta, bypass_cache: bool = False
    ) -> Optional[LyricResult]:
        logger.info(f"Musixmatch-Spotify: fetching lyrics for {track.display_name()}")
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                lrc = await _fetch_macro(
                    client,
                    {
                        "track_spotify_id": track.trackid,  # type: ignore[dict-item]
                        "usertoken": MUSIXMATCH_USERTOKEN,
                    },
                )
        except Exception as e:
            logger.error(f"Musixmatch-Spotify: fetch failed: {e}")
            return LyricResult(status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR)

        if lrc is None:
            logger.debug(
                f"Musixmatch-Spotify: no lyrics found for {track.display_name()}"
            )
            return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

        logger.info(f"Musixmatch-Spotify: got SUCCESS_SYNCED lyrics ({len(lrc)} lines)")
        return LyricResult(
            status=CacheStatus.SUCCESS_SYNCED,
            lyrics=lrc,
            source=self.source_name,
        )


class MusixmatchFetcher(BaseFetcher):
    """Metadata search + multi-candidate fallback."""

    @property
    def source_name(self) -> str:
        return "musixmatch"

    def is_available(self, track: TrackMeta) -> bool:
        return bool(track.title) and bool(MUSIXMATCH_USERTOKEN)

    async def _search(self, track: TrackMeta) -> tuple[Optional[int], float]:
        params: dict[str, str] = {
            "format": "json",
            "app_id": "web-desktop-app-v1.0",
            "q_track": track.title or "",
            "usertoken": MUSIXMATCH_USERTOKEN,
            "page_size": "10",
            "f_has_lyrics": "1",
        }
        if track.artist:
            params["q_artist"] = track.artist
        if track.album:
            params["q_album"] = track.album

        url = f"{MUSIXMATCH_SEARCH_URL}?{urlencode(params)}"
        logger.debug(f"Musixmatch: searching for '{track.display_name()}'")

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.get(url, headers=_MXM_HEADERS)
                resp.raise_for_status()
                data = resp.json()

            track_list = data.get("message", {}).get("body", {}).get("track_list", [])
            if not isinstance(track_list, list) or not track_list:
                logger.debug("Musixmatch: search returned 0 results")
                return None, 0.0

            logger.debug(f"Musixmatch: search returned {len(track_list)} candidates")

            candidates = [
                SearchCandidate(
                    item=int(t["commontrack_id"]),
                    duration_ms=(
                        float(t["track_length"]) * 1000
                        if t.get("track_length")
                        else None
                    ),
                    is_synced=bool(t.get("has_subtitles") or t.get("has_richsync")),
                    title=t.get("track_name"),
                    artist=t.get("artist_name"),
                    album=t.get("album_name"),
                )
                for item in track_list
                if isinstance(item, dict)
                and isinstance(t := item.get("track", {}), dict)
                and isinstance(t.get("commontrack_id"), int)
                and not t.get("instrumental")
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
                    f"Musixmatch: best candidate id={best_id} ({confidence:.0f})"
                )
            else:
                logger.debug("Musixmatch: no suitable candidate found")
            return best_id, confidence

        except Exception as e:
            logger.error(f"Musixmatch: search failed: {e}")
            return None, 0.0

    async def fetch(
        self, track: TrackMeta, bypass_cache: bool = False
    ) -> Optional[LyricResult]:
        logger.info(f"Musixmatch: fetching lyrics for {track.display_name()}")
        commontrack_id, confidence = await self._search(track)
        if commontrack_id is None:
            logger.debug(f"Musixmatch: no match found for {track.display_name()}")
            return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                lrc = await _fetch_macro(
                    client,
                    {
                        "commontrack_id": str(commontrack_id),
                        "usertoken": MUSIXMATCH_USERTOKEN,
                    },
                )
        except Exception as e:
            logger.error(f"Musixmatch: fetch failed: {e}")
            return LyricResult(status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR)

        if lrc is None:
            logger.debug(f"Musixmatch: no lyrics for commontrack_id={commontrack_id}")
            return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

        logger.info(
            f"Musixmatch: got SUCCESS_SYNCED lyrics "
            f"for commontrack_id={commontrack_id} ({len(lrc)} lines)"
        )
        return LyricResult(
            status=CacheStatus.SUCCESS_SYNCED,
            lyrics=lrc,
            source=self.source_name,
            confidence=confidence,
        )
