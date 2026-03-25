"""Local fetcher — reads lyrics from .lrc sidecar files or embedded audio metadata.

Priority:
  1. Same-directory .lrc file (e.g. /path/to/track.lrc)
  2. Embedded lyrics in audio metadata (FLAC, MP3 USLT/SYLT tags)
"""

import re
import os
from typing import Optional
from loguru import logger
from lrcfetch.models import TrackMeta, LyricResult, CacheStatus
from lrcfetch.fetchers.base import BaseFetcher
from mutagen._file import File
from mutagen.flac import FLAC

# Matches LRC time tags like [00:12.34] or [01:23.456]
_LRC_TIME_TAG_RE = re.compile(r"\[\d{2}:\d{2}\.\d{2,3}\]")
# Matches time tags that are all zeros
_ZERO_TIME_TAG_RE = re.compile(r"^\[00:00\.0{2,3}\]$")


def _detect_sync_status(text: str) -> CacheStatus:
    """Determine whether lyrics text contains meaningful LRC time tags.

    Returns UNSYNCED if no tags exist or all tags are [00:00.00].
    """
    tags = _LRC_TIME_TAG_RE.findall(text)
    if not tags:
        return CacheStatus.SUCCESS_UNSYNCED
    for tag in tags:
        if not _ZERO_TIME_TAG_RE.match(tag):
            return CacheStatus.SUCCESS_SYNCED
    return CacheStatus.SUCCESS_UNSYNCED


class LocalFetcher(BaseFetcher):
    @property
    def source_name(self) -> str:
        return "local"

    def fetch(self, track: TrackMeta) -> Optional[LyricResult]:
        """Attempt to read lyrics from local filesystem."""
        if not track.is_local or not track.url:
            return None

        file_path = track.url.replace("file://", "", 1)
        if not os.path.exists(file_path):
            logger.debug(f"Local: file does not exist: {file_path}")
            return None

        logger.info(f"Local: checking for lyrics near {file_path}")

        # Sidecar .lrc file
        lrc_path = os.path.splitext(file_path)[0] + ".lrc"
        if os.path.exists(lrc_path):
            try:
                with open(lrc_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    status = _detect_sync_status(content)
                    logger.info(f"Local: found .lrc sidecar ({status.value})")
                    return LyricResult(
                        status=status, lyrics=content, source=self.source_name
                    )
            except Exception as e:
                logger.error(f"Local: error reading {lrc_path}: {e}")

        # Embedded metadata
        try:
            audio = File(file_path)
            if audio is not None:
                lyrics = None

                if isinstance(audio, FLAC):
                    # FLAC stores lyrics in vorbis comment tags
                    lyrics = (audio.get("lyrics") or audio.get("unsynclyrics") or [None])[0]
                elif hasattr(audio, "tags") and audio.tags:
                    # MP3 / other: look for USLT or SYLT ID3 frames
                    for key in audio.tags.keys():
                        if key.startswith("USLT") or key.startswith("SYLT"):
                            lyrics = str(audio.tags[key])
                            break

                if lyrics:
                    status = _detect_sync_status(lyrics)
                    logger.info(f"Local: found embedded lyrics ({status.value})")
                    return LyricResult(
                        status=status,
                        lyrics=lyrics.strip(),
                        source=f"{self.source_name} (embedded)",
                    )
                else:
                    logger.debug("Local: no embedded lyrics found")
        except Exception as e:
            logger.error(f"Local: error reading metadata for {file_path}: {e}")

        logger.debug(f"Local: no lyrics found for {file_path}")
        return None
