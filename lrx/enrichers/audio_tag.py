"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-31 06:11:27
Description: Enricher that reads metadata from audio file tags (mutagen)
"""

from typing import Optional
from loguru import logger
from mutagen._file import File, FileType

from .base import BaseEnricher
from ..models import TrackMeta
from ..lrc import get_audio_path


class AudioTagEnricher(BaseEnricher):
    """Extract title, artist, album, and duration from audio file tags."""

    @property
    def name(self) -> str:
        return "audio-tag"

    def enrich(self, track: TrackMeta) -> Optional[dict]:
        if not track.is_local or not track.url:
            return None

        audio_path = get_audio_path(track.url, ensure_exists=True)
        if not audio_path:
            return None

        try:
            audio = File(audio_path)
        except Exception as e:
            logger.debug(f"AudioTag: failed to read {audio_path}: {e}")
            return None

        if audio is None:
            return None

        updates: dict = {}

        # Try common tag names (vorbis comments, ID3, MP4)
        title = _first_tag(audio, "title", "TIT2", "\xa9nam")
        if title and not track.title:
            updates["title"] = title

        artist = _first_tag(audio, "artist", "TPE1", "\xa9ART")
        if artist and not track.artist:
            updates["artist"] = artist

        album = _first_tag(audio, "album", "TALB", "\xa9alb")
        if album and not track.album:
            updates["album"] = album

        if not track.length and audio.info and hasattr(audio.info, "length"):
            length_ms = int(audio.info.length * 1000)
            if length_ms > 0:
                updates["length"] = length_ms

        if updates:
            logger.debug(f"AudioTag: enriched fields: {list(updates.keys())}")
        return updates or None


def _first_tag(audio: FileType, *keys: str) -> Optional[str]:
    """Return the first non-empty string value found among the given tag keys."""
    if not audio.tags:
        return None
    for key in keys:
        val = audio.tags.get(key)
        if val is None:
            continue
        # mutagen returns lists for vorbis, single values for ID3
        if isinstance(val, list):
            val = val[0] if val else None
        if val:
            return str(val).strip()
    return None
