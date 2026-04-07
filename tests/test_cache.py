from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lrx_cli.cache import (
    CacheEngine,
    SLOT_SYNCED,
    SLOT_UNSYNCED,
    _generate_key,
)
from lrx_cli.config import DURATION_TOLERANCE_MS
from lrx_cli.models import CacheStatus, LyricResult, TrackMeta
from lrx_cli.lrc import LRCData


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
    return LyricResult(status=status, lyrics=LRCData(lyrics), source=source)


@pytest.fixture
def cache_db(tmp_path: Path) -> CacheEngine:
    db_path = tmp_path / "cache.db"
    return CacheEngine(str(db_path))


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


def test_migrate_adds_confidence_version_and_boosts_unsynced(tmp_path: Path) -> None:
    """Legacy single-row cache is migrated to slot rows.

    Expected behavior:
    - add positive_kind and confidence_version
    - boost SUCCESS_UNSYNCED confidence by +10 with cap at 100
    - keep SUCCESS_SYNCED confidence unchanged
    """
    db_path = tmp_path / "legacy-cache.db"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE cache (
                key TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                lyrics TEXT,
                created_at INTEGER NOT NULL,
                expires_at INTEGER,
                artist TEXT,
                title TEXT,
                album TEXT,
                length INTEGER,
                confidence REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO cache
                (key, source, status, lyrics, created_at, expires_at, artist, title, album, length, confidence)
            VALUES
                ('u1', 's1', 'SUCCESS_UNSYNCED', 'u1', 1, NULL, 'A', 'T', 'AL', 180000, 85.0),
                ('u2', 's2', 'SUCCESS_UNSYNCED', 'u2', 1, NULL, 'A', 'T', 'AL', 180000, 98.0),
                ('s1', 's3', 'SUCCESS_SYNCED', 's1', 1, NULL, 'A', 'T', 'AL', 180000, 70.0)
            """
        )
        conn.commit()

    CacheEngine(str(db_path))

    with sqlite3.connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(cache)").fetchall()}
        rows = conn.execute(
            "SELECT key, positive_kind, status, confidence, confidence_version FROM cache ORDER BY key, positive_kind"
        ).fetchall()

    assert "positive_kind" in cols
    assert "confidence_version" in cols
    by_key = {
        (k, slot): (status, confidence, version)
        for k, slot, status, confidence, version in rows
    }
    assert by_key[("u1", SLOT_UNSYNCED)] == ("SUCCESS_UNSYNCED", 95.0, 1)
    assert by_key[("u2", SLOT_UNSYNCED)] == ("SUCCESS_UNSYNCED", 100.0, 1)
    assert by_key[("s1", SLOT_SYNCED)] == ("SUCCESS_SYNCED", 70.0, 1)


def test_migrate_negative_row_splits_into_two_slot_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-negative.db"

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE cache (
                key TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                lyrics TEXT,
                created_at INTEGER NOT NULL,
                expires_at INTEGER,
                artist TEXT,
                title TEXT,
                album TEXT,
                length INTEGER,
                confidence REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO cache
                (key, source, status, lyrics, created_at, expires_at, artist, title, album, length, confidence)
            VALUES
                ('n1', 's1', 'NOT_FOUND', NULL, 1, NULL, 'A', 'T', 'AL', 180000, 0.0)
            """
        )
        conn.commit()

    CacheEngine(str(db_path))

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT key, positive_kind, status FROM cache ORDER BY positive_kind"
        ).fetchall()

    assert rows == [
        ("n1", SLOT_SYNCED, "NOT_FOUND"),
        ("n1", SLOT_UNSYNCED, "NOT_FOUND"),
    ]


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

    cached_rows = cache_db.get_all(track, "lrclib")

    assert len(cached_rows) == 1
    cached = cached_rows[0]
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
    cached_rows = cache_db.get_all(track, "netease")

    assert cached_rows == []
    assert cache_db.query_all() == []


def test_set_negative_without_slot_writes_both_slots(cache_db: CacheEngine) -> None:
    track = _track()
    cache_db.set(
        track, "src", _result(CacheStatus.NOT_FOUND, None, "src"), ttl_seconds=60
    )

    with sqlite3.connect(cache_db.db_path) as conn:
        rows = conn.execute(
            "SELECT positive_kind, status FROM cache ORDER BY positive_kind"
        ).fetchall()

    assert rows == [
        (SLOT_SYNCED, CacheStatus.NOT_FOUND.value),
        (SLOT_UNSYNCED, CacheStatus.NOT_FOUND.value),
    ]


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
    cached_rows = cache_db.get_all(track_with_length, "spotify")

    assert cached_rows

    with sqlite3.connect(cache_db.db_path) as conn:
        row = conn.execute("SELECT length FROM cache LIMIT 1").fetchone()
    assert row is not None
    assert row[0] == 200000


def test_get_best_prefers_higher_confidence_and_skips_negative(
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


def test_find_best_positive_returns_status_specific_results(
    cache_db: CacheEngine,
) -> None:
    track = _track(artist="Artist", title="Song", album="Album")
    cache_db.set(track, "u-high", _result(CacheStatus.SUCCESS_UNSYNCED, "u", "u-high"))
    cache_db.set(track, "s-low", _result(CacheStatus.SUCCESS_SYNCED, "s", "s-low"))
    cache_db.update_confidence(track, 95.0, "u-high")
    cache_db.update_confidence(track, 70.0, "s-low")

    best_synced = cache_db.find_best_positive(track, CacheStatus.SUCCESS_SYNCED)
    assert best_synced is not None
    assert best_synced.status is CacheStatus.SUCCESS_SYNCED
    assert str(best_synced.lyrics) == "s"
    assert best_synced.source == "cache-search"

    best_unsynced = cache_db.find_best_positive(track, CacheStatus.SUCCESS_UNSYNCED)
    assert best_unsynced is not None
    assert best_unsynced.status is CacheStatus.SUCCESS_UNSYNCED
    assert str(best_unsynced.lyrics) == "u"


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
        title="  hello world  ",
        length=200000,
    )

    sources = [r["source"] for r in rows]
    assert "negative" not in sources
    assert "far-len" not in sources
    assert "close-unsynced" in sources
    # Sorted by duration diff, then confidence for equal diff.
    assert sources[0] == "seed"
    assert sources[1] == "close-synced"
    assert sources[2] == "close-unsynced"
    # Unknown length remains candidate with fallback distance priority.
    assert sources[-1] == "unknown-len"


def test_update_confidence_targets_specific_source(cache_db: CacheEngine) -> None:
    track = _track(artist="A", title="T", album="AL")
    cache_db.set(track, "s1", _result(CacheStatus.SUCCESS_SYNCED, "x", "s1"))
    cache_db.set(track, "s2", _result(CacheStatus.SUCCESS_UNSYNCED, "y", "s2"))

    updated = cache_db.update_confidence(track, 75.0, "s1")

    assert updated == 1
    rows = {r["source"]: r for r in cache_db.query_track(track)}
    assert rows["s1"]["confidence"] == 75.0
    assert rows["s2"]["confidence"] == 100.0  # unchanged default


def test_update_confidence_updates_both_slots_for_same_source(
    cache_db: CacheEngine,
) -> None:
    track = _track(artist="A", title="T", album="AL")
    cache_db.set(
        track,
        "src",
        _result(CacheStatus.SUCCESS_SYNCED, "sync", "src"),
        positive_kind=SLOT_SYNCED,
    )
    cache_db.set(
        track,
        "src",
        _result(CacheStatus.SUCCESS_UNSYNCED, "unsync", "src"),
        positive_kind=SLOT_UNSYNCED,
    )

    updated = cache_db.update_confidence(track, 66.0, "src")
    assert updated == 2

    with sqlite3.connect(cache_db.db_path) as conn:
        rows = conn.execute(
            "SELECT positive_kind, confidence FROM cache WHERE source = 'src' ORDER BY positive_kind"
        ).fetchall()

    assert rows == [(SLOT_SYNCED, 66.0), (SLOT_UNSYNCED, 66.0)]


def test_update_confidence_returns_zero_for_missing_source(
    cache_db: CacheEngine,
) -> None:
    track = _track(artist="A", title="T", album="AL")
    cache_db.set(track, "s1", _result(CacheStatus.SUCCESS_SYNCED, "x", "s1"))

    assert cache_db.update_confidence(track, 50.0, "nonexistent") == 0


def test_update_confidence_returns_zero_for_empty_track(
    cache_db: CacheEngine,
) -> None:
    empty = _track(artist=None, title=None, album=None, length=None)
    assert cache_db.update_confidence(empty, 50.0, "s1") == 0


def test_credential_set_and_get_roundtrip(cache_db: CacheEngine) -> None:
    cache_db.set_credential("spotify", {"access_token": "tok", "expires_in": 3600})
    result = cache_db.get_credential("spotify")
    assert result == {"access_token": "tok", "expires_in": 3600}


def test_credential_get_returns_none_on_miss(cache_db: CacheEngine) -> None:
    assert cache_db.get_credential("nonexistent") is None


def test_credential_expires_at_respected(
    monkeypatch: pytest.MonkeyPatch, cache_db: CacheEngine
) -> None:
    # Store with expiry 1000 ms in the future
    now_ms = 5_000_000_000
    monkeypatch.setattr("lrx_cli.cache.time.time", lambda: now_ms / 1000)
    cache_db.set_credential(
        "musixmatch", {"user_token": "abc"}, expires_at_ms=now_ms + 1000
    )

    # Still valid
    assert cache_db.get_credential("musixmatch") == {"user_token": "abc"}

    # Advance past expiry
    monkeypatch.setattr("lrx_cli.cache.time.time", lambda: (now_ms + 2000) / 1000)
    assert cache_db.get_credential("musixmatch") is None


def test_credential_no_expiry_never_expires(
    monkeypatch: pytest.MonkeyPatch, cache_db: CacheEngine
) -> None:
    cache_db.set_credential("spotify", {"token": "forever"}, expires_at_ms=None)
    monkeypatch.setattr("lrx_cli.cache.time.time", lambda: 9_999_999_999.0)
    assert cache_db.get_credential("spotify") == {"token": "forever"}


def test_credential_set_overwrites_existing(cache_db: CacheEngine) -> None:
    cache_db.set_credential("spotify", {"token": "old"})
    cache_db.set_credential("spotify", {"token": "new"})
    assert cache_db.get_credential("spotify") == {"token": "new"}


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
    assert stats["by_slot"][SLOT_SYNCED] == 1
    assert stats["by_slot"][SLOT_UNSYNCED] == 1
