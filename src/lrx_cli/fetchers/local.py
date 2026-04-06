"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-26 02:08:41
Description: Local fetcher — reads lyrics from .lrc sidecar files or embedded audio metadata.
             Priority:
               1. Same-directory .lrc file (e.g. /path/to/track.lrc)
               2. Embedded lyrics in audio metadata (FLAC, MP3 USLT/SYLT tags)
"""

from typing import Optional
from loguru import logger
from mutagen._file import File
from mutagen.flac import FLAC

from .base import BaseFetcher
from ..models import TrackMeta, LyricResult
from ..lrc import get_audio_path, get_sidecar_path, LRCData


class LocalFetcher(BaseFetcher):
    @property
    def source_name(self) -> str:
        return "local"

    def is_available(self, track: TrackMeta) -> bool:
        return track.is_local

    async def fetch(
        self, track: TrackMeta, bypass_cache: bool = False
    ) -> Optional[LyricResult]:
        """Attempt to read lyrics from local filesystem."""
        if not track.is_local or not track.url:
            return None

        audio_path = get_audio_path(track.url, ensure_exists=False)
        if not audio_path:
            logger.debug(f"Local: audio URL is not a valid file path: {track.url}")
            return None

        lrc_path = get_sidecar_path(
            track.url, ensure_audio_exists=False, ensure_exists=True
        )
        if lrc_path:
            try:
                with open(lrc_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    lrc = LRCData(content)
                    status = lrc.detect_sync_status()
                    logger.info(
                        f"Local: found .lrc sidecar ({status.value}) for {audio_path.name}"
                    )
                    return LyricResult(
                        status=status,
                        lyrics=lrc,
                        source=self.source_name,
                    )
            except Exception as e:
                logger.error(f"Local: error reading {lrc_path}: {e}")
        else:
            logger.debug(f"Local: no .lrc sidecar found for {audio_path}")

        # Embedded metadata
        if not audio_path.exists():
            logger.debug(f"Local: audio file does not exist: {audio_path}")
            return None
        try:
            audio = File(audio_path)
            if audio is not None:
                lyrics = None

                if isinstance(audio, FLAC):
                    # FLAC stores lyrics in vorbis comment tags
                    lyrics = (
                        audio.get("lyrics") or audio.get("unsynclyrics") or [None]
                    )[0]
                elif hasattr(audio, "tags") and audio.tags:
                    # MP3 / other: look for USLT or SYLT ID3 frames
                    for key in audio.tags.keys():
                        if key.startswith("USLT") or key.startswith("SYLT"):
                            lyrics = str(audio.tags[key])
                            break

                if lyrics:
                    lrc = LRCData(lyrics)
                    status = lrc.detect_sync_status()
                    logger.info(
                        f"Local: found embedded lyrics ({status.value}) for {audio_path.name}"
                    )
                    return LyricResult(
                        status=status,
                        lyrics=lrc,
                        source=f"{self.source_name} (embedded)",
                    )
                else:
                    logger.debug("Local: no embedded lyrics found")
        except Exception as e:
            logger.error(f"Local: error reading metadata for {audio_path}: {e}")

        logger.debug(f"Local: no lyrics found for {audio_path}")
        return None
