from __future__ import annotations

from lrx_cli.fetchers.selection import SearchCandidate, select_best


def test_picks_closest_duration_within_tolerance() -> None:
    candidates = [
        SearchCandidate(item="far", duration_ms=10000.0),
        SearchCandidate(item="close", duration_ms=5100.0),
        SearchCandidate(item="exact", duration_ms=5000.0),
    ]
    assert select_best(candidates, track_length_ms=5000) == "exact"


def test_filters_out_candidates_beyond_tolerance() -> None:
    candidates = [
        SearchCandidate(item="too_far", duration_ms=100000.0),
    ]
    assert select_best(candidates, track_length_ms=5000, tolerance_ms=2000) is None


def test_prefers_synced_at_equal_duration() -> None:
    candidates = [
        SearchCandidate(item="unsynced", duration_ms=5000.0, is_synced=False),
        SearchCandidate(item="synced", duration_ms=5000.0, is_synced=True),
    ]
    assert select_best(candidates, track_length_ms=5000) == "synced"


def test_closer_duration_wins_over_synced() -> None:
    candidates = [
        SearchCandidate(item="synced_far", duration_ms=6000.0, is_synced=True),
        SearchCandidate(item="unsynced_close", duration_ms=5001.0, is_synced=False),
    ]
    assert select_best(candidates, track_length_ms=5000) == "unsynced_close"


def test_skips_candidates_without_duration_when_track_length_given() -> None:
    candidates = [
        SearchCandidate(item="no_dur", duration_ms=None),
        SearchCandidate(item="has_dur", duration_ms=5000.0),
    ]
    assert select_best(candidates, track_length_ms=5000) == "has_dur"


def test_returns_none_when_all_lack_duration_and_track_length_given() -> None:
    candidates = [
        SearchCandidate(item="a", duration_ms=None),
        SearchCandidate(item="b", duration_ms=None),
    ]
    assert select_best(candidates, track_length_ms=5000) is None


def test_prefers_synced_when_no_track_length() -> None:
    candidates = [
        SearchCandidate(item="unsynced", is_synced=False),
        SearchCandidate(item="synced", is_synced=True),
    ]
    assert select_best(candidates, track_length_ms=None) == "synced"


def test_falls_back_to_first_when_none_synced() -> None:
    candidates = [
        SearchCandidate(item="first"),
        SearchCandidate(item="second"),
    ]
    assert select_best(candidates, track_length_ms=None) == "first"


def test_empty_candidates_returns_none() -> None:
    assert select_best([], track_length_ms=5000) is None
    assert select_best([], track_length_ms=None) is None


def test_single_candidate_within_tolerance() -> None:
    candidates = [SearchCandidate(item="only", duration_ms=5000.0)]
    assert select_best(candidates, track_length_ms=5000) == "only"


def test_single_candidate_beyond_tolerance() -> None:
    candidates = [SearchCandidate(item="only", duration_ms=99999.0)]
    assert select_best(candidates, track_length_ms=5000, tolerance_ms=1000) is None


def test_generic_type_preserved() -> None:
    """select_best returns the same type as SearchCandidate.item."""
    int_candidates = [SearchCandidate(item=42, duration_ms=5000.0)]
    assert select_best(int_candidates, track_length_ms=5000) == 42

    dict_candidates = [SearchCandidate(item={"id": 1}, duration_ms=5000.0)]
    result = select_best(dict_candidates, track_length_ms=5000)
    assert result == {"id": 1}
