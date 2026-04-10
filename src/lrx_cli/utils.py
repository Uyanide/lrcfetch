"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-04-10 17:06:37
Description: Utility functions
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional
from urllib.parse import unquote
from pathlib import Path

from .models import CacheStatus

if TYPE_CHECKING:
    from .models import LyricResult


# Paths


def get_audio_path(audio_url: str, ensure_exists: bool = False) -> Optional[Path]:
    """Convert file:// URL to Path, return None if invalid or (if ensure_exists) file doesn't exist."""
    if not audio_url.startswith("file://"):
        return None
    file_path = unquote(audio_url.replace("file://", "", 1))
    path = Path(file_path)
    if ensure_exists and not path.exists():
        return None
    return path


def get_sidecar_path(
    audio_url: str,
    ensure_audio_exists: bool = False,
    ensure_exists: bool = False,
    extension: str = ".lrc",
) -> Optional[Path]:
    """Given a file:// URL, return the corresponding .lrc sidecar path.

    If ensure_audio_exists is True, return None if the audio file does not exist.
    If ensure_exists is True, return None if the .lrc file does not exist.
    """
    audio_path = get_audio_path(audio_url, ensure_exists=ensure_audio_exists)
    if not audio_path:
        return None
    lrc_path = audio_path.with_suffix(extension)
    if ensure_exists and not lrc_path.exists():
        return None
    return lrc_path


# Ranking


def is_positive_status(status: CacheStatus) -> bool:
    return status in (CacheStatus.SUCCESS_SYNCED, CacheStatus.SUCCESS_UNSYNCED)


def is_better_result(
    new: LyricResult,
    old: LyricResult,
    *,
    allow_unsynced: bool,
) -> bool:
    """Return True when new should rank above old.

    Ordering rules (highest first):
    1) Positive statuses always beat negative statuses.
    2) When allow_unsynced=False, SUCCESS_SYNCED always beats SUCCESS_UNSYNCED.
    3) Higher confidence beats lower confidence.
    4) On equal confidence, SUCCESS_SYNCED beats SUCCESS_UNSYNCED.
    """
    new_positive = is_positive_status(new.status)
    old_positive = is_positive_status(old.status)

    if not new_positive:
        return False
    if not old_positive:
        return True

    new_synced = new.status == CacheStatus.SUCCESS_SYNCED
    old_synced = old.status == CacheStatus.SUCCESS_SYNCED

    if not allow_unsynced and new_synced != old_synced:
        return new_synced

    if new.confidence != old.confidence:
        return new.confidence > old.confidence

    return new_synced and not old_synced


def select_best_positive(
    candidates: list[LyricResult],
    *,
    allow_unsynced: bool,
) -> Optional[LyricResult]:
    """Pick best positive LyricResult from candidates.

    Negative statuses are ignored.
    """
    positives = [c for c in candidates if is_positive_status(c.status)]
    if not positives:
        return None

    best = positives[0]
    for c in positives[1:]:
        if is_better_result(c, best, allow_unsynced=allow_unsynced):
            best = c
    return best
