"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 04:09:36
Description: Data models
"""

from pydantic import BaseModel, ConfigDict
from enum import Enum
from typing import Optional


class CacheStatus(str, Enum):
    """Status of a cached lyric entry."""

    SUCCESS_SYNCED = "SUCCESS_SYNCED"
    SUCCESS_UNSYNCED = "SUCCESS_UNSYNCED"
    NOT_FOUND = "NOT_FOUND"
    NETWORK_ERROR = "NETWORK_ERROR"


class TrackMeta(BaseModel):
    """Metadata describing a track obtained from MPRIS or manual input."""

    model_config = ConfigDict(strict=True)

    trackid: Optional[str] = None  # Spotify track ID (without "spotify:track:" prefix)
    length: Optional[int] = None  # Duration in milliseconds
    album: Optional[str] = None
    artist: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None  # Playback URL (file:// for local files)

    @property
    def is_local(self) -> bool:
        """True when the track is a local file (file:// URL)."""
        return bool(self.url and self.url.startswith("file://"))

    @property
    def is_complete(self) -> bool:
        """True when all fields required by LRCLIB are present."""
        return all([self.length, self.album, self.title, self.artist])

    def display_name(self) -> str:
        """Human-readable representation for logging."""
        parts = []
        if self.artist:
            parts.append(self.artist)
        if self.title:
            parts.append(self.title)
        return " - ".join(parts) if parts else self.trackid or self.url or "(unknown)"


class LyricResult(BaseModel):
    """Result of a lyric fetch attempt, also used as cache record."""

    model_config = ConfigDict(strict=True)

    status: CacheStatus
    lyrics: Optional[str] = None
    source: Optional[str] = None  # Which fetcher produced this result
    ttl: Optional[int] = None  # Hint for cache TTL (seconds)
