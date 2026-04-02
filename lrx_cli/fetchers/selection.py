"""
Shared candidate-selection logic for search-based fetchers.

Each fetcher maps its API-specific results to SearchCandidate, then calls
select_best() which scores candidates by metadata similarity, duration
proximity, and sync status.
"""

from dataclasses import dataclass
from typing import Generic, Optional, TypeVar

from ..config import (
    DURATION_TOLERANCE_MS,
    SCORE_W_TITLE as _W_TITLE,
    SCORE_W_ARTIST as _W_ARTIST,
    SCORE_W_ALBUM as _W_ALBUM,
    SCORE_W_DURATION as _W_DURATION,
    SCORE_W_SYNCED as _W_SYNCED,
    MIN_CONFIDENCE,
)
from ..normalize import normalize_for_match, normalize_artist

T = TypeVar("T")


@dataclass
class SearchCandidate(Generic[T]):
    """A normalized search result for best-match selection.

    Attributes:
        item: The original API-specific object (dict, ID, etc.)
        duration_ms: Track duration in milliseconds, or None if unknown.
        is_synced: Whether this candidate is known to have synced lyrics.
        title: Candidate track title for similarity scoring.
        artist: Candidate artist name for similarity scoring.
        album: Candidate album name for similarity scoring.
    """

    item: T
    duration_ms: Optional[float] = None
    is_synced: bool = False
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None


def _text_similarity(a: str, b: str) -> float:
    """Compare two normalized strings. Returns 0.0-1.0."""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    # Containment: one is a substring of the other (e.g. "My Love" vs "My Love (Album Version)")
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    return 0.0


def _score_candidate(
    c: SearchCandidate[T],
    ref_title: Optional[str],
    ref_artist: Optional[str],
    ref_album: Optional[str],
    ref_length_ms: Optional[int],
) -> float:
    """Score a candidate from 0-100 based on metadata match quality.

    Scoring works in two tiers:

    1. **Metadata score** — computed from fields available on *both* sides,
       then rescaled to fill the 0-90 range so that missing fields don't
       inflate the score.  Fields missing on both sides are simply excluded
       from the calculation (neutral).  Fields present on only one side
       contribute 0 to the numerator but their weight still counts in the
       denominator (penalty for asymmetric absence).

    2. **Synced bonus** — a flat 10 pts, always applied independently.

    Field weights (before rescaling):
      - Title:    40
      - Artist:   30
      - Album:    10
      - Duration: 10
    """
    raw = 0.0
    available_weight = 0.0

    # Title
    if ref_title is not None or c.title is not None:
        available_weight += _W_TITLE
        if ref_title is not None and c.title is not None:
            raw += _W_TITLE * _text_similarity(
                normalize_for_match(ref_title), normalize_for_match(c.title)
            )
    # else both None → excluded

    # Artist
    if ref_artist is not None or c.artist is not None:
        available_weight += _W_ARTIST
        if ref_artist is not None and c.artist is not None:
            na = normalize_artist(ref_artist)
            nb = normalize_artist(c.artist)
            if na == nb:
                raw += _W_ARTIST
            else:
                raw += _W_ARTIST * _text_similarity(
                    normalize_for_match(ref_artist), normalize_for_match(c.artist)
                )

    # Album
    if ref_album is not None or c.album is not None:
        available_weight += _W_ALBUM
        if ref_album is not None and c.album is not None:
            raw += _W_ALBUM * _text_similarity(
                normalize_for_match(ref_album), normalize_for_match(c.album)
            )

    # Duration
    if ref_length_ms is not None or c.duration_ms is not None:
        available_weight += _W_DURATION
        if ref_length_ms is not None and c.duration_ms is not None:
            diff = abs(c.duration_ms - ref_length_ms)
            if diff <= DURATION_TOLERANCE_MS:
                raw += _W_DURATION * (1.0 - diff / DURATION_TOLERANCE_MS)

    # Rescale metadata to 0-90 range
    _MAX_METADATA = _W_TITLE + _W_ARTIST + _W_ALBUM + _W_DURATION  # 90
    if available_weight > 0:
        metadata_score = (raw / available_weight) * _MAX_METADATA
    else:
        # No comparable fields at all — only synced bonus matters
        metadata_score = 0.0

    # Synced bonus (always 10 pts, independent of metadata)
    synced_score = _W_SYNCED if c.is_synced else 0.0

    return metadata_score + synced_score


def select_best(
    candidates: list[SearchCandidate[T]],
    track_length_ms: Optional[int] = None,
    *,
    title: Optional[str] = None,
    artist: Optional[str] = None,
    album: Optional[str] = None,
    min_confidence: float = MIN_CONFIDENCE,
) -> tuple[Optional[T], float]:
    """Pick the best candidate by confidence scoring.

    Returns (item, score). Item is None if no candidate scores above min_confidence.
    """
    if not candidates:
        return None, 0.0

    best_item: Optional[T] = None
    best_score = -1.0

    for c in candidates:
        s = _score_candidate(c, title, artist, album, track_length_ms)
        if s > best_score:
            best_score = s
            best_item = c.item

    if best_score < min_confidence:
        return None, best_score

    return best_item, best_score
