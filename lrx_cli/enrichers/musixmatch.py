"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-04-05 02:13:49
Description: Musixmatch metadata enricher (matcher.track.get by Spotify track ID)
"""

from typing import Optional
from urllib.parse import urlencode

import httpx
from loguru import logger

from .base import BaseEnricher
from ..models import TrackMeta
from ..config import (
    HTTP_TIMEOUT,
    MUSIXMATCH_TRACK_MATCH_URL,
    MUSIXMATCH_USERTOKEN,
)

_MXM_HEADERS = {"Cookie": "x-mxm-token-guid="}
_MXM_TRACK_MATCH_BASE_PARAMS = {
    "format": "json",
    "app_id": "web-desktop-app-v1.0",
    "usertoken": MUSIXMATCH_USERTOKEN,
}


class MusixmatchSpotifyEnricher(BaseEnricher):
    """Fill title, artist, album, and length from Musixmatch using Spotify track ID."""

    @property
    def name(self) -> str:
        return "musixmatch"

    @property
    def provides(self) -> set[str]:
        return {"title", "artist", "album", "length"}

    async def enrich(self, track: TrackMeta) -> Optional[dict]:
        if not track.trackid or not MUSIXMATCH_USERTOKEN:
            return None

        params = {
            **_MXM_TRACK_MATCH_BASE_PARAMS,
            "track_spotify_id": track.trackid,
        }
        url = f"{MUSIXMATCH_TRACK_MATCH_URL}?{urlencode(params)}"
        logger.debug(f"Musixmatch enricher: looking up trackid={track.trackid}")

        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.get(url, headers=_MXM_HEADERS)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(f"Musixmatch enricher: request failed: {e}")
            return None

        body = data.get("message", {}).get("body")
        t = body.get("track") if isinstance(body, dict) else None
        if not isinstance(t, dict):
            logger.debug(
                f"Musixmatch enricher: no track data for trackid={track.trackid}"
            )
            return None

        updates: dict = {}
        if isinstance(t.get("track_name"), str) and t["track_name"]:
            updates["title"] = t["track_name"]
        if isinstance(t.get("artist_name"), str) and t["artist_name"]:
            updates["artist"] = t["artist_name"]
        if isinstance(t.get("album_name"), str) and t["album_name"]:
            updates["album"] = t["album_name"]
        if isinstance(t.get("track_length"), int) and t["track_length"] > 0:
            updates["length"] = t["track_length"] * 1000

        if updates:
            logger.debug(f"Musixmatch enricher: filled {list(updates.keys())}")
        return updates or None
