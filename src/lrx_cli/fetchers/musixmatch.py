"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-04-04 15:28:34
Description: Musixmatch fetchers (desktop API, anonymous or usertoken auth).

             Uses the Musixmatch desktop API (apic-desktop.musixmatch.com).
             Token and all HTTP calls are managed by MusixmatchAuthenticator.

             Two fetchers:
             musixmatch-spotify  — direct lookup by Spotify track ID (exact, no search)
             musixmatch          — metadata search + best-candidate fallback
"""

import json
from typing import Optional
from loguru import logger

from .base import BaseFetcher, FetchResult
from .selection import SearchCandidate, select_best
from ..authenticators.musixmatch import MusixmatchAuthenticator
from ..config import GeneralConfig
from ..lrc import LRCData
from ..models import CacheStatus, LyricResult, TrackMeta

_MUSIXMATCH_MACRO_URL = "https://apic-desktop.musixmatch.com/ws/1.1/macro.subtitles.get"
_MUSIXMATCH_SEARCH_URL = "https://apic-desktop.musixmatch.com/ws/1.1/track.search"

# Macro-specific params (format/app_id injected by authenticator)
_MXM_MACRO_PARAMS = {
    "namespace": "lyrics_richsynched",
    "subtitle_format": "mxm",
    "optional_calls": "track.richsync",
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
    auth: MusixmatchAuthenticator,
    params: dict,
) -> Optional[LRCData]:
    """Call macro.subtitles.get via auth.get_json.

    Returns LRCData (richsync preferred over subtitle), or None when no usable
    lyrics are found. Raises on HTTP/network errors.
    """
    logger.debug(f"Musixmatch: macro call with {list(params.keys())}")
    data = await auth.get_json(_MUSIXMATCH_MACRO_URL, {**_MXM_MACRO_PARAMS, **params})
    if data is None:
        return None

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

    _auth: MusixmatchAuthenticator

    def __init__(self, general: GeneralConfig, auth: MusixmatchAuthenticator) -> None:
        super().__init__(general, auth)

    @property
    def source_name(self) -> str:
        return "musixmatch-spotify"

    def is_available(self, track: TrackMeta) -> bool:
        return bool(track.trackid) and not self._auth.is_cooldown()

    async def fetch(self, track: TrackMeta, bypass_cache: bool = False) -> FetchResult:
        logger.info(f"Musixmatch-Spotify: fetching lyrics for {track.display_name()}")

        try:
            lrc = await _fetch_macro(
                self._auth,
                {"track_spotify_id": track.trackid},  # type: ignore[dict-item]
            )
        except AttributeError:
            return FetchResult.from_not_found()
        except Exception as e:
            logger.error(f"Musixmatch-Spotify: fetch failed: {e}")
            return FetchResult.from_network_error()

        if lrc is None:
            logger.debug(
                f"Musixmatch-Spotify: no lyrics found for {track.display_name()}"
            )
            return FetchResult.from_not_found()

        logger.info(f"Musixmatch-Spotify: got SUCCESS_SYNCED lyrics ({len(lrc)} lines)")
        return FetchResult(
            synced=LyricResult(
                status=CacheStatus.SUCCESS_SYNCED,
                lyrics=lrc,
                source=self.source_name,
            ),
            # Fetching unsynced lyrics is not possible with current endpoint,
            # so no need to cache NOT_FOUND to avoid repeated failed attempts
            unsynced=None,
        )


class MusixmatchFetcher(BaseFetcher):
    """Metadata search + best-candidate lyric fetch."""

    _auth: MusixmatchAuthenticator

    def __init__(self, general: GeneralConfig, auth: MusixmatchAuthenticator) -> None:
        super().__init__(general, auth)

    @property
    def source_name(self) -> str:
        return "musixmatch"

    @property
    def requires_auth(self) -> str:
        return "musixmatch"

    def is_available(self, track: TrackMeta) -> bool:
        return bool(track.title) and not self._auth.is_cooldown()

    async def _search(self, track: TrackMeta) -> tuple[Optional[int], float]:
        """Search for track metadata. Raises on network/HTTP errors."""
        params: dict = {
            "q_track": track.title or "",
            "page_size": "10",
            "f_has_lyrics": "1",
        }
        if track.artist:
            params["q_artist"] = track.artist
        if track.album:
            params["q_album"] = track.album

        logger.debug(f"Musixmatch: searching for '{track.display_name()}'")
        data = await self._auth.get_json(_MUSIXMATCH_SEARCH_URL, params)
        if data is None:
            return None, 0.0

        track_list = data.get("message", {}).get("body", {}).get("track_list", [])
        if not isinstance(track_list, list) or not track_list:
            logger.debug("Musixmatch: search returned 0 results")
            return None, 0.0

        logger.debug(f"Musixmatch: search returned {len(track_list)} candidates")

        candidates = [
            SearchCandidate(
                item=int(t["commontrack_id"]),
                duration_ms=(
                    float(t["track_length"]) * 1000 if t.get("track_length") else None
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
            logger.debug(f"Musixmatch: best candidate id={best_id} ({confidence:.0f})")
        else:
            logger.debug("Musixmatch: no suitable candidate found")
        return best_id, confidence

    async def fetch(self, track: TrackMeta, bypass_cache: bool = False) -> FetchResult:
        logger.info(f"Musixmatch: fetching lyrics for {track.display_name()}")

        try:
            commontrack_id, confidence = await self._search(track)
            if commontrack_id is None:
                logger.debug(f"Musixmatch: no match found for {track.display_name()}")
                return FetchResult.from_not_found()

            lrc = await _fetch_macro(
                self._auth,
                {"commontrack_id": str(commontrack_id)},
            )
        except AttributeError:
            return FetchResult.from_not_found()
        except Exception as e:
            logger.error(f"Musixmatch: fetch failed: {e}")
            return FetchResult.from_network_error()

        if lrc is None:
            logger.debug(f"Musixmatch: no lyrics for commontrack_id={commontrack_id}")
            return FetchResult.from_not_found()

        logger.info(
            f"Musixmatch: got SUCCESS_SYNCED lyrics "
            f"for commontrack_id={commontrack_id} ({len(lrc)} lines)"
        )
        return FetchResult(
            synced=LyricResult(
                status=CacheStatus.SUCCESS_SYNCED,
                lyrics=lrc,
                source=self.source_name,
                confidence=confidence,
            ),
            # Same as above
            unsynced=None,
        )
