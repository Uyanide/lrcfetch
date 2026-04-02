from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lrx_cli.cache import (
    CacheEngine,
    _generate_key,
    _normalize_artist,
    _normalize_for_match,
)
from lrx_cli.config import DURATION_TOLERANCE_MS
from lrx_cli.models import CacheStatus, LyricResult, TrackMeta


def _track(
    *,
    artist: str | None = "Artist",
    title: str | None = "Song",
    album: str | None = "Album",
    length: int | None = 180000,
    trackid: str | None = None,
    url: str | None = None,
) -> TrackMeta:
    return TrackMeta(
        artist=artist,
        title=title,
        album=album,
        length=length,
        trackid=trackid,
        url=url,
    )


def _result(
    status: CacheStatus,
    lyrics: str | None,
    source: str,
) -> LyricResult:
    return LyricResult(status=status, lyrics=lyrics, source=source)


@pytest.fixture
def cache_db(tmp_path: Path) -> CacheEngine:
    db_path = tmp_path / "cache.db"
    return CacheEngine(str(db_path))


def test_normalize_for_match_covers_nfkc_punct_feat_and_whitespace() -> None:
    text = "  Ｔｅｓｔ！  feat. SOMEONE  "

    normalized = _normalize_for_match(text)

    assert normalized == "test"


def test_normalize_artist_splits_separators_and_sorts_parts() -> None:
    artist = "B / A feat. C; D vs. E × F 、 G"

    normalized = _normalize_artist(artist)

    assert normalized == "a\0b\0d\0e\0f\0g"


def test_generate_key_uses_spotify_trackid_and_url_fallback() -> None:
    spotify_track = _track(
        trackid="abc123", artist=None, title=None, album=None, length=None
    )
    local_track = _track(
        artist=None, title=None, album=None, length=None, url="file:///x.flac"
    )

    assert _generate_key(spotify_track, "spotify") == "spotify:abc123"
    assert _generate_key(local_track, "local") == "local:url:file:///x.flac"


def test_generate_key_raises_when_metadata_missing() -> None:
    with pytest.raises(ValueError):
        _generate_key(
            _track(artist=None, title=None, album=None, length=None, url=None), "lrclib"
        )


def test_set_and_get_roundtrip_with_ttl(
    monkeypatch: pytest.MonkeyPatch, cache_db: CacheEngine
) -> None:
    monkeypatch.setattr("lrx_cli.cache.time.time", lambda: 1_000_000)

    track = _track()
    cache_db.set(
        track,
        "lrclib",
        _result(CacheStatus.SUCCESS_SYNCED, "[00:01.00]line", "lrclib"),
        ttl_seconds=120,
    )

    cached = cache_db.get(track, "lrclib")

    assert cached is not None
    assert cached.status is CacheStatus.SUCCESS_SYNCED
    assert str(cached.lyrics) == "[00:01.00]line"
    assert cached.source == "lrclib"
    assert cached.ttl == 120


def test_get_expired_entry_returns_none_and_removes_row(
    monkeypatch: pytest.MonkeyPatch, cache_db: CacheEngine
) -> None:
    track = _track()
    monkeypatch.setattr("lrx_cli.cache.time.time", lambda: 2_000_000)
    cache_db.set(
        track,
        "netease",
        _result(CacheStatus.SUCCESS_UNSYNCED, "line", "netease"),
        ttl_seconds=10,
    )

    monkeypatch.setattr("lrx_cli.cache.time.time", lambda: 2_000_020)
    cached = cache_db.get(track, "netease")

    assert cached is None
    assert cache_db.query_all() == []


def test_get_backfills_missing_length_when_track_provides_it(
    cache_db: CacheEngine,
) -> None:
    track_without_length = _track(
        trackid="spotify-track-1",
        artist=None,
        title=None,
        album=None,
        length=None,
    )
    cache_db.set(
        track_without_length,
        "spotify",
        _result(CacheStatus.SUCCESS_SYNCED, "line", "spotify"),
    )

    track_with_length = _track(
        trackid="spotify-track-1",
        artist=None,
        title=None,
        album=None,
        length=200000,
    )
    cached = cache_db.get(track_with_length, "spotify")

    assert cached is not None

    with sqlite3.connect(cache_db.db_path) as conn:
        row = conn.execute("SELECT length FROM cache LIMIT 1").fetchone()
    assert row is not None
    assert row[0] == 200000


def test_get_best_prefers_synced_over_unsynced_and_negative(
    cache_db: CacheEngine,
) -> None:
    track = _track()
    cache_db.set(
        track,
        "source-a",
        _result(CacheStatus.NOT_FOUND, None, "source-a"),
    )
    cache_db.set(
        track,
        "source-b",
        _result(CacheStatus.SUCCESS_UNSYNCED, "unsynced", "source-b"),
    )
    cache_db.set(
        track,
        "source-c",
        _result(CacheStatus.SUCCESS_SYNCED, "synced", "source-c"),
    )

    best = cache_db.get_best(track, ["source-a", "source-b", "source-c"])

    assert best is not None
    assert best.status is CacheStatus.SUCCESS_SYNCED
    assert str(best.lyrics) == "synced"


def test_clear_track_and_clear_all_affect_expected_rows(cache_db: CacheEngine) -> None:
    track_a = _track(artist="A", title="T", album="X")
    track_b = _track(artist="B", title="T", album="X")

    cache_db.set(track_a, "s1", _result(CacheStatus.SUCCESS_SYNCED, "a1", "s1"))
    cache_db.set(track_a, "s2", _result(CacheStatus.SUCCESS_UNSYNCED, "a2", "s2"))
    cache_db.set(track_b, "s1", _result(CacheStatus.SUCCESS_SYNCED, "b1", "s1"))

    cache_db.clear_track(track_a)
    rows_after_track_clear = cache_db.query_all()
    assert len(rows_after_track_clear) == 1
    assert rows_after_track_clear[0]["artist"] == "B"

    cache_db.clear_all()
    assert cache_db.query_all() == []


def test_prune_removes_only_expired_rows(
    monkeypatch: pytest.MonkeyPatch, cache_db: CacheEngine
) -> None:
    track = _track()
    monkeypatch.setattr("lrx_cli.cache.time.time", lambda: 3_000_000)
    cache_db.set(
        track,
        "s-expired",
        _result(CacheStatus.SUCCESS_SYNCED, "x", "s-expired"),
        ttl_seconds=1,
    )
    cache_db.set(
        track,
        "s-active",
        _result(CacheStatus.SUCCESS_SYNCED, "y", "s-active"),
        ttl_seconds=100,
    )

    monkeypatch.setattr("lrx_cli.cache.time.time", lambda: 3_000_010)
    deleted = cache_db.prune()

    assert deleted == 1
    rows = cache_db.query_all()
    assert len(rows) == 1
    assert rows[0]["source"] == "s-active"


def test_find_best_positive_uses_exact_match_and_prefers_synced(
    cache_db: CacheEngine,
) -> None:
    track = _track(artist="Artist", title="Song", album="Album")
    cache_db.set(track, "s1", _result(CacheStatus.SUCCESS_UNSYNCED, "u", "s1"))
    cache_db.set(track, "s2", _result(CacheStatus.SUCCESS_SYNCED, "s", "s2"))

    best = cache_db.find_best_positive(track)

    assert best is not None
    assert best.status is CacheStatus.SUCCESS_SYNCED
    assert str(best.lyrics) == "s"
    # find_best_positive always reports cache-search source
    assert best.source == "cache-search"


def test_search_by_meta_fuzzy_rules_and_duration_sorting(cache_db: CacheEngine) -> None:
    # Same logical title/artist after normalization, different length quality.
    base = _track(
        artist="A / B",
        title="Hello，World!",
        album="Album",
        length=200000,
    )
    close_synced = _track(
        artist="B vs. A",
        title="hello world",
        album="Else",
        length=200500,
    )
    close_unsynced = _track(
        artist="A feat. C / B",
        title="HELLO WORLD",
        album="Else2",
        length=201000,
    )
    unknown_len = _track(
        artist="A & B",
        title="Hello World",
        album="Else3",
        length=None,
    )
    far_len = _track(
        artist="A / B",
        title="Hello World",
        album="Else4",
        length=200000 + DURATION_TOLERANCE_MS + 1,
    )

    cache_db.set(base, "seed", _result(CacheStatus.SUCCESS_SYNCED, "seed", "seed"))
    cache_db.set(
        close_synced,
        "close-synced",
        _result(CacheStatus.SUCCESS_SYNCED, "cs", "close-synced"),
    )
    cache_db.set(
        close_unsynced,
        "close-unsynced",
        _result(CacheStatus.SUCCESS_UNSYNCED, "cu", "close-unsynced"),
    )
    cache_db.set(
        unknown_len,
        "unknown-len",
        _result(CacheStatus.SUCCESS_SYNCED, "ul", "unknown-len"),
    )
    cache_db.set(
        far_len,
        "far-len",
        _result(CacheStatus.SUCCESS_SYNCED, "fl", "far-len"),
    )
    # Negative status should never appear in search results.
    cache_db.set(
        _track(artist="A / B", title="Hello World", album="Else5", length=200000),
        "negative",
        _result(CacheStatus.NOT_FOUND, None, "negative"),
    )

    rows = cache_db.search_by_meta(
        artist="Ｂ ; A",
        title="  hello world  ",
        length=200000,
    )

    sources = [r["source"] for r in rows]
    assert "negative" not in sources
    assert "far-len" not in sources
    # Sorted by duration diff, then synced before unsynced for equal diff.
    assert sources[0] == "seed"
    assert sources[1] == "close-synced"
    assert sources[2] == "close-unsynced"
    # Unknown length remains candidate with fallback distance priority.
    assert sources[-1] == "unknown-len"


def test_query_track_and_stats_return_expected_aggregates(
    cache_db: CacheEngine,
) -> None:
    cache_db.set(
        _track(artist="A", title="T", album="AL"),
        "s1",
        _result(CacheStatus.SUCCESS_SYNCED, "x", "s1"),
    )
    cache_db.set(
        _track(artist="A", title="T", album="AL"),
        "s2",
        _result(CacheStatus.SUCCESS_UNSYNCED, "y", "s2"),
    )

    rows = cache_db.query_track(_track(artist="A", title="T", album="AL"))
    stats = cache_db.stats()

    assert len(rows) == 2
    assert stats["total"] == 2
    assert stats["active"] == 2
    assert stats["expired"] == 0
    assert stats["by_status"][CacheStatus.SUCCESS_SYNCED.value] == 1
    assert stats["by_status"][CacheStatus.SUCCESS_UNSYNCED.value] == 1
