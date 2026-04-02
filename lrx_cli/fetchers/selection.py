"""
Shared candidate-selection logic for search-based fetchers.

Each fetcher maps its API-specific results to SearchCandidate, then calls
select_best() which handles duration filtering and synced preference uniformly.
"""

from dataclasses import dataclass
from typing import Generic, Optional, TypeVar

from ..config import DURATION_TOLERANCE_MS

T = TypeVar("T")


@dataclass
class SearchCandidate(Generic[T]):
    """A normalized search result for best-match selection.

    Attributes:
        item: The original API-specific object (dict, ID, etc.)
        duration_ms: Track duration in milliseconds, or None if unknown.
        is_synced: Whether this candidate is known to have synced lyrics.
    """

    item: T
    duration_ms: Optional[float] = None
    is_synced: bool = False


def select_best(
    candidates: list[SearchCandidate[T]],
    track_length_ms: Optional[int] = None,
    tolerance_ms: float = DURATION_TOLERANCE_MS,
) -> Optional[T]:
    """Pick the best candidate by duration proximity and sync preference.

    When track_length_ms is available:
      - Filter by tolerance_ms
      - Pick closest duration, prefer synced at equal distance
    When track_length_ms is unavailable:
      - Pick first synced candidate, or first overall
    """
    if track_length_ms is not None:
        best: Optional[SearchCandidate[T]] = None
        best_diff = float("inf")

        for c in candidates:
            if c.duration_ms is None:
                continue
            diff = abs(c.duration_ms - track_length_ms)
            if diff > tolerance_ms:
                continue
            if diff < best_diff or (
                diff == best_diff
                and c.is_synced
                and (best is None or not best.is_synced)
            ):
                best_diff = diff
                best = c

        return best.item if best is not None else None

    # No duration — prefer synced, fallback to first
    for c in candidates:
        if c.is_synced:
            return c.item
    return candidates[0].item if candidates else None
