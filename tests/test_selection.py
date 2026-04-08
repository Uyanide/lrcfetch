from __future__ import annotations

from lrx_cli.fetchers.selection import (
    SearchCandidate,
    select_best,
    select_ranked,
    _score_candidate,
    _text_similarity,
    MIN_CONFIDENCE,
)


def test_text_similarity_exact() -> None:
    assert _text_similarity("my love", "my love") == 1.0


def test_text_similarity_empty() -> None:
    assert _text_similarity("", "anything") == 0.0
    assert _text_similarity("anything", "") == 0.0


def test_text_similarity_no_overlap() -> None:
    assert _text_similarity("hello", "world") == 0.0


def test_text_similarity_containment() -> None:
    # "my love" is contained in "my love album version"
    score = _text_similarity("my love", "my love album version")
    assert 0.0 < score < 1.0
    assert score == len("my love") / len("my love album version")


def test_score_perfect_match() -> None:
    """Exact metadata + close duration + synced = 100."""
    c = SearchCandidate(
        item="x",
        duration_ms=232000.0,
        is_synced=True,
        title="My Love",
        artist="Westlife",
        album="Coast To Coast",
    )
    score = _score_candidate(c, "My Love", "Westlife", "Coast To Coast", 232000)
    assert score == 100.0


def test_score_no_metadata_match() -> None:
    """Completely wrong metadata should score very low."""
    c = SearchCandidate(
        item="x",
        duration_ms=192000.0,
        is_synced=True,
        title="Let My Love Be Your Pillow (Live)",
        artist="Ronnie Milsap",
        album="The Essential Ronnie Milsap",
    )
    score = _score_candidate(c, "My Love", "Westlife", "Coast To Coast", 232000)
    assert score < MIN_CONFIDENCE


def test_score_missing_both_sides_neutral() -> None:
    """If neither ref nor candidate has any field, only synced bonus applies."""
    c = SearchCandidate(item="x", is_synced=True)
    score = _score_candidate(c, None, None, None, None)
    # No comparable fields → metadata = 0, synced = 10
    assert score == 10.0


def test_score_missing_one_side_gives_zero_for_field() -> None:
    """If ref has title but candidate doesn't, title gets 0 and weight still counts."""
    c = SearchCandidate(item="x", title=None, is_synced=True)
    # Only title is in play (weight=40), candidate missing → raw=0, rescaled=0, + synced=10
    score = _score_candidate(c, "My Love", None, None, None)
    assert score == 10.0


def test_synced_state_does_not_affect_score() -> None:
    base = SearchCandidate(item="x", title="My Love", is_synced=False)
    synced = SearchCandidate(item="x", title="My Love", is_synced=True)
    diff = _score_candidate(synced, "My Love", None, None, None) - _score_candidate(
        base, "My Love", None, None, None
    )
    assert diff == 0.0


def test_score_duration_linear_decay() -> None:
    """Duration score decays linearly; ratios between exact/half/edge are preserved."""
    exact = SearchCandidate(item="x", duration_ms=232000.0)
    score_exact = _score_candidate(exact, None, None, None, 232000)

    half_tol = SearchCandidate(item="x", duration_ms=232000.0 + 1500.0)
    score_half = _score_candidate(half_tol, None, None, None, 232000)

    at_tol = SearchCandidate(item="x", duration_ms=232000.0 + 3000.0)
    score_edge = _score_candidate(at_tol, None, None, None, 232000)

    # Only duration is comparable → metadata spans 0-90, plus a constant baseline +10
    # exact=100, half=55, edge=10
    assert score_exact == 100.0
    assert score_half == 55.0
    assert score_edge == 10.0


def test_duration_hard_filter_rejects_all_mismatched() -> None:
    """All candidates outside duration tolerance are filtered before scoring."""
    candidates = [
        SearchCandidate(
            item="wrong", duration_ms=180000.0, title="My Love", artist="Westlife"
        ),
        SearchCandidate(
            item="also-wrong", duration_ms=300000.0, title="My Love", artist="Westlife"
        ),
    ]
    best, _ = select_best(candidates, 232000, title="My Love", artist="Westlife")
    assert best is None


def test_duration_neutral_when_ref_has_no_duration() -> None:
    """Candidate duration does not penalise when the reference has no duration."""
    # Candidate A: title only (no duration)
    c_no_dur = SearchCandidate(item="no-dur", title="My Love")
    # Candidate B: same title + a duration (ref has none)
    c_with_dur = SearchCandidate(item="with-dur", title="My Love", duration_ms=232000.0)
    score_no_dur = _score_candidate(c_no_dur, "My Love", None, None, None)
    score_with_dur = _score_candidate(c_with_dur, "My Love", None, None, None)
    assert score_no_dur == score_with_dur


def test_score_case_insensitive_title() -> None:
    c = SearchCandidate(item="x", title="my love")
    s1 = _score_candidate(c, "My Love", None, None, None)
    s2 = _score_candidate(c, "my love", None, None, None)
    assert s1 == s2


def test_score_artist_normalization() -> None:
    """'Westlife feat. Someone' should still match 'Westlife'."""
    c = SearchCandidate(item="x", artist="Westlife feat. Someone")
    # normalize_artist strips feat. → both become "westlife"
    score = _score_candidate(c, None, "Westlife", None, None)
    assert score >= 30.0  # full artist weight (30) when both None on other fields


# Reference track: Westlife - My Love, album Coast To Coast, ~232s
_REF_TITLE = "My Love"
_REF_ARTIST = "Westlife"
_REF_ALBUM = "Coast To Coast"
_REF_LENGTH = 232000  # ms


def _lrclib_candidates() -> list[SearchCandidate[dict]]:
    """Fixtures from real LRCLIB search results."""
    raw = [
        {
            "trackName": "My Love",
            "artistName": "Westlife",
            "albumName": "null",
            "duration": 232.0,
            "synced": True,
        },
        {
            "trackName": "My Love",
            "artistName": "Westlife",
            "albumName": "null",
            "duration": 180.0,
            "synced": True,
        },
        {
            "trackName": "My love",
            "artistName": "Westlife",
            "albumName": "moments",
            "duration": 235.327,
            "synced": True,
        },
        {
            "trackName": "My Love",
            "artistName": "Westlife",
            "albumName": "Unbreakable",
            "duration": 233.026,
            "synced": True,
        },
        {
            "trackName": "My Love",
            "artistName": "Westlife",
            "albumName": "Coast To Coast",
            "duration": 231.847,
            "synced": True,
        },
        {
            "trackName": "Hello My Love",
            "artistName": "Westlife",
            "albumName": "Spectrum",
            "duration": 216.0,
            "synced": True,
        },
        {
            "trackName": "My Love",
            "artistName": "Westlife",
            "albumName": "Hitzone 13",
            "duration": 231.0,
            "synced": True,
        },
    ]
    return [
        SearchCandidate(
            item=r,
            duration_ms=r["duration"] * 1000,
            is_synced=r["synced"],
            title=r["trackName"],
            artist=r["artistName"],
            album=r["albumName"],
        )
        for r in raw
    ]


def _lrclib_noisy_candidates() -> list[SearchCandidate[dict]]:
    """Fixtures from LRCLIB title-only search — lots of wrong artists."""
    raw = [
        {
            "trackName": "Let My Love Be Your Pillow (Live)",
            "artistName": "Ronnie Milsap",
            "albumName": "The Essential Ronnie Milsap",
            "duration": 192.0,
            "synced": True,
        },
        {
            "trackName": "My Love",
            "artistName": "Little Texas",
            "albumName": "Big Time",
            "duration": 248.0,
            "synced": True,
        },
        {
            "trackName": "My Love (Album Version)",
            "artistName": "Little Texas",
            "albumName": "Greatest Hits",
            "duration": 248.0,
            "synced": True,
        },
        {
            "trackName": "My Love - Digitally Remastered '89",
            "artistName": "Sonny James",
            "albumName": "Capitol Collectors Series",
            "duration": 169.0,
            "synced": False,
        },
        {
            "trackName": "My Love",
            "artistName": "Westlife",
            "albumName": "Coast To Coast",
            "duration": 231.847,
            "synced": True,
        },
    ]
    return [
        SearchCandidate(
            item=r,
            duration_ms=r["duration"] * 1000,
            is_synced=r["synced"],
            title=r["trackName"],
            artist=r["artistName"],
            album=r["albumName"],
        )
        for r in raw
    ]


def _netease_candidates() -> list[SearchCandidate[int]]:
    """Fixtures from real Netease search results."""
    raw = [
        {
            "id": 2080607,
            "name": "My Love",
            "artist": "Westlife",
            "album": "Unbreakable, Vol. 1 - The Greatest Hits",
            "dt": 231941,
        },
        {
            "id": 2080749,
            "name": "My Love (Radio Edit)",
            "artist": "Westlife",
            "album": "World Of Our Own - No. 1 Hits Plus (EP)",
            "dt": 232920,
        },
        {
            "id": 29809886,
            "name": "My Love (Live)",
            "artist": "Westlife",
            "album": "The Farewell Tour: Live at Croke Park",
            "dt": 262000,
        },
        {
            "id": 572412968,
            "name": "My Love",
            "artist": "Westlife",
            "album": "Pure... Love",
            "dt": 231000,
        },
        {
            "id": 20707713,
            "name": "You Raise Me Up",
            "artist": "Westlife",
            "album": "You Raise Me Up",
            "dt": 241116,
        },
    ]
    return [
        SearchCandidate(
            item=r["id"],
            duration_ms=float(r["dt"]),
            title=r["name"],
            artist=r["artist"],
            album=r["album"],
        )
        for r in raw
    ]


def test_lrclib_picks_exact_album_match() -> None:
    """With full metadata, should pick the Coast To Coast entry."""
    candidates = _lrclib_candidates()
    best, score = select_best(
        candidates,
        _REF_LENGTH,
        title=_REF_TITLE,
        artist=_REF_ARTIST,
        album=_REF_ALBUM,
    )
    assert best is not None
    assert best["albumName"] == "Coast To Coast"
    assert score >= MIN_CONFIDENCE


def test_lrclib_noisy_picks_westlife() -> None:
    """In noisy title-only results, artist matching should filter to Westlife."""
    candidates = _lrclib_noisy_candidates()
    best, _ = select_best(
        candidates,
        _REF_LENGTH,
        title=_REF_TITLE,
        artist=_REF_ARTIST,
        album=_REF_ALBUM,
    )
    assert best is not None
    assert best["artistName"] == "Westlife"


def test_lrclib_noisy_rejects_all_without_ref_artist() -> None:
    """Without ref artist, wrong-artist candidates may still win, but right title should rank higher."""
    candidates = _lrclib_noisy_candidates()
    best, _ = select_best(
        candidates,
        _REF_LENGTH,
        title=_REF_TITLE,
    )
    # Should pick a "My Love" over "Let My Love Be Your Pillow"
    assert best is not None
    assert "My Love" == best["trackName"] or best["trackName"].startswith("My Love")


def test_netease_picks_closest_duration() -> None:
    candidates = _netease_candidates()
    best, _ = select_best(
        candidates,
        _REF_LENGTH,
        title=_REF_TITLE,
        artist=_REF_ARTIST,
        album=_REF_ALBUM,
    )
    # 2080607 has dt=231941 (diff=59ms), closest to 232000
    assert best == 2080607


def test_netease_rejects_wrong_title() -> None:
    """'You Raise Me Up' should not be selected."""
    candidates = _netease_candidates()
    best, _ = select_best(
        candidates,
        _REF_LENGTH,
        title=_REF_TITLE,
        artist=_REF_ARTIST,
    )
    assert best != 20707713


def test_netease_without_ref_metadata_rejects_below_confidence() -> None:
    """Without any ref metadata, candidates with one-sided fields score low and get rejected."""
    candidates = _netease_candidates()
    best, _ = select_best(candidates, _REF_LENGTH)
    # Candidates have title/artist/album but ref has None for all → 0 for text fields
    # Only duration (max 10) contributes → below MIN_CONFIDENCE (25)
    assert best is None


def test_empty_candidates_returns_none() -> None:
    assert select_best([], track_length_ms=5000) == (None, 0.0)
    assert select_best([], track_length_ms=None) == (None, 0.0)


def test_all_below_min_confidence_returns_none() -> None:
    """If all candidates score below threshold, return None."""
    candidates = [
        SearchCandidate(
            item="x",
            title="Completely Different Song",
            artist="Unknown Artist",
            album="Unknown Album",
            duration_ms=999999.0,
        ),
    ]
    result, _ = select_best(
        candidates,
        232000,
        title="My Love",
        artist="Westlife",
        album="Coast To Coast",
        min_confidence=90.0,
    )
    assert result is None


def test_generic_type_preserved() -> None:
    int_candidates = [SearchCandidate(item=42, duration_ms=5000.0, title="x")]
    best, _ = select_best(int_candidates, 5000, title="x")
    assert best == 42

    dict_candidates = [SearchCandidate(item={"id": 1}, title="x")]
    best, _ = select_best(dict_candidates, title="x")
    assert best == {"id": 1}


def test_select_ranked_empty_input() -> None:
    assert select_ranked([]) == []


def test_select_ranked_all_below_confidence() -> None:
    """All candidates below threshold → empty list."""
    candidates = [
        SearchCandidate(item="x", title="Completely Different", duration_ms=999999.0)
    ]
    result = select_ranked(
        candidates, 232000, title="My Love", artist="Westlife", min_confidence=90.0
    )
    assert result == []


def test_select_ranked_sorted_descending() -> None:
    """Results are ordered highest score first."""
    candidates = _netease_candidates()
    ranked = select_ranked(
        candidates,
        _REF_LENGTH,
        title=_REF_TITLE,
        artist=_REF_ARTIST,
        album=_REF_ALBUM,
    )
    assert len(ranked) >= 2
    scores = [score for _, score in ranked]
    assert scores == sorted(scores, reverse=True)


def test_select_ranked_respects_max_results() -> None:
    candidates = _netease_candidates()
    ranked = select_ranked(
        candidates,
        _REF_LENGTH,
        title=_REF_TITLE,
        artist=_REF_ARTIST,
        album=_REF_ALBUM,
        max_results=2,
    )
    assert len(ranked) <= 2


def test_select_ranked_consistent_with_select_best() -> None:
    """First result of select_ranked matches select_best."""
    candidates = _netease_candidates()
    kwargs = dict(title=_REF_TITLE, artist=_REF_ARTIST, album=_REF_ALBUM)
    ranked = select_ranked(candidates, _REF_LENGTH, **kwargs)  # type: ignore
    best_item, best_score = select_best(candidates, _REF_LENGTH, **kwargs)  # type: ignore
    assert ranked[0] == (best_item, best_score)


def test_select_ranked_duration_hard_filter_applies() -> None:
    """Candidates outside duration tolerance are excluded from ranked results."""
    candidates = _netease_candidates()
    ranked = select_ranked(
        candidates,
        _REF_LENGTH,
        title=_REF_TITLE,
        artist=_REF_ARTIST,
        album=_REF_ALBUM,
    )
    ids = [item for item, _ in ranked]
    # 29809886 (dt=262000, diff=30000ms) and 20707713 (dt=241116, diff=9116ms)
    # both exceed DURATION_TOLERANCE_MS=3000 → must not appear
    assert 29809886 not in ids
    assert 20707713 not in ids


def test_select_ranked_netease_top_is_best_duration_match() -> None:
    """2080607 (diff=59ms) should rank first over 572412968 (diff=1000ms)."""
    candidates = _netease_candidates()
    ranked = select_ranked(
        candidates,
        _REF_LENGTH,
        title=_REF_TITLE,
        artist=_REF_ARTIST,
        album=_REF_ALBUM,
    )
    assert ranked[0][0] == 2080607
