"""Shared ranking rules for LyricResult selection.

This module centralizes how positive lyric results are compared so cache/core
and other callers use the same precedence and edge-case handling.
"""

from __future__ import annotations

from typing import Optional

from .models import CacheStatus, LyricResult


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
