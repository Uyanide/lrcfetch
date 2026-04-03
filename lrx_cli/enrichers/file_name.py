"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-31 06:08:44
Description: Enricher that parses metadata from the audio file path
"""

import re
from typing import Optional
from loguru import logger

from .base import BaseEnricher
from ..models import TrackMeta
from ..lrc import get_audio_path


# Common track-number prefixes: "01 - ", "01. ", "1 - ", etc.
_TRACK_NUM_RE = re.compile(r"^\d{1,3}[\s.\-]+")


class FileNameEnricher(BaseEnricher):
    """Derive artist / title from the file path when tags are unavailable.

    Heuristics (applied to the stem of the filename):
      - "Artist - Title"  →  artist, title
      - "01 - Title"      →  title only (leading track number stripped)
      - "Title"           →  title only

    If artist is still missing after parsing the filename, the parent
    directory name is used as a guess (common layout: ``Artist/Album/track``).
    """

    @property
    def name(self) -> str:
        return "file-name"

    def enrich(self, track: TrackMeta) -> Optional[dict]:
        if not track.is_local or not track.url:
            return None

        audio_path = get_audio_path(track.url, ensure_exists=False)
        if not audio_path:
            return None

        updates: dict = {}
        stem = audio_path.stem

        # Try "Artist - Title" split
        if " - " in stem:
            left, right = stem.split(" - ", 1)
            left = _TRACK_NUM_RE.sub("", left).strip()
            right = right.strip()

            if left and right:
                # Both sides non-empty after stripping track number
                if not track.artist:
                    updates["artist"] = left
                if not track.title:
                    updates["title"] = right
            elif right:
                # Left was only a track number → right is the title
                if not track.title:
                    updates["title"] = right

        # Try "Artist-Title" split (no spaces)
        elif "-" in stem:
            left, right = stem.split("-", 1)
            left = _TRACK_NUM_RE.sub("", left).strip()
            right = right.strip()

            if left and right:
                if not track.artist:
                    updates["artist"] = left
                if not track.title:
                    updates["title"] = right
            elif right:
                if not track.title:
                    updates["title"] = right

        # No separator: strip track number, remainder is title
        else:
            title_guess = _TRACK_NUM_RE.sub("", stem).strip()
            if title_guess and not track.title:
                updates["title"] = title_guess

        # Use parent directory as album fallback
        if not track.album and "album" not in updates:
            parents = audio_path.parents
            if len(parents) >= 1:
                album_dir = parents[0].name
                if album_dir and album_dir not in (".", "/"):
                    if not track.album:
                        updates["album"] = album_dir

        if updates:
            logger.debug(f"FileName: enriched fields: {list(updates.keys())}")
        return updates or None
