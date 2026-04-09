from __future__ import annotations

import asyncio
from pathlib import Path

from lrx_cli.config import AppConfig
from lrx_cli.enrichers.audio_tag import AudioTagEnricher
from lrx_cli.enrichers.file_name import FileNameEnricher
from lrx_cli.models import CacheStatus, TrackMeta
from lrx_cli.fetchers.local import LocalFetcher

_GENERAL = AppConfig().general


def _local_track(path: Path) -> TrackMeta:
    return TrackMeta(url=f"file://{path}")


def test_local_fetcher_unavailable_for_non_local_track():
    fetcher = LocalFetcher(_GENERAL)
    assert not fetcher.is_available(TrackMeta(title="Song", artist="Artist"))


def test_local_fetcher_available_for_local_track(tmp_path):
    fetcher = LocalFetcher(_GENERAL)
    assert fetcher.is_available(_local_track(tmp_path / "song.flac"))


def test_local_fetcher_returns_empty_for_non_file_url():
    fetcher = LocalFetcher(_GENERAL)
    track = TrackMeta(url="https://example.com/song.mp3")
    result = asyncio.run(fetcher.fetch(track))
    assert result.synced is None and result.unsynced is None


def test_local_fetcher_reads_synced_sidecar(tmp_path):
    audio = tmp_path / "song.flac"
    lrc = audio.with_suffix(".lrc")
    lrc.write_text("[00:01.00]Hello\n[00:03.00]World\n")

    fetcher = LocalFetcher(_GENERAL)
    result = asyncio.run(fetcher.fetch(_local_track(audio)))

    assert result.synced is not None
    assert result.synced.status == CacheStatus.SUCCESS_SYNCED
    assert result.synced.source is not None
    assert "sidecar" in result.synced.source


def test_local_fetcher_reads_unsynced_sidecar(tmp_path):
    audio = tmp_path / "song.flac"
    lrc = audio.with_suffix(".lrc")
    lrc.write_text("Hello\nWorld\n")

    fetcher = LocalFetcher(_GENERAL)
    result = asyncio.run(fetcher.fetch(_local_track(audio)))

    assert result.unsynced is not None
    assert result.synced is None


def test_local_fetcher_empty_sidecar_ignored(tmp_path):
    audio = tmp_path / "song.flac"
    (audio.with_suffix(".lrc")).write_text("   ")

    fetcher = LocalFetcher(_GENERAL)
    result = asyncio.run(fetcher.fetch(_local_track(audio)))

    assert result.synced is None and result.unsynced is None


def _enrich(path: str, **existing) -> dict | None:
    enricher = FileNameEnricher()
    track = TrackMeta(url=f"file://{path}", **existing)
    return asyncio.run(enricher.enrich(track))


def test_filename_enricher_artist_title_split(tmp_path):
    result = _enrich(str(tmp_path / "Utada Hikaru - First Love.flac"))
    assert result == {
        "artist": "Utada Hikaru",
        "title": "First Love",
        "album": tmp_path.name,
    }


def test_filename_enricher_track_number_prefix(tmp_path):
    # "01. Title" — no " - " separator, regex strips leading "01. "
    result = _enrich(str(tmp_path / "01. First Love.flac"))
    assert result and result.get("title") == "First Love"
    assert "artist" not in result


def test_filename_enricher_title_only(tmp_path):
    result = _enrich(str(tmp_path / "First Love.flac"))
    assert result and result.get("title") == "First Love"


def test_filename_enricher_does_not_overwrite_existing_fields(tmp_path):
    result = _enrich(
        str(tmp_path / "Artist - Title.flac"),
        artist="Existing Artist",
        title="Existing Title",
    )
    assert result is None or ("artist" not in result and "title" not in result)


def test_filename_enricher_non_local_returns_none():
    enricher = FileNameEnricher()
    track = TrackMeta(title="Song", artist="Artist")
    assert asyncio.run(enricher.enrich(track)) is None


def test_audio_tag_enricher_non_local_returns_none():
    enricher = AudioTagEnricher()
    track = TrackMeta(title="Song", artist="Artist")
    assert asyncio.run(enricher.enrich(track)) is None


def test_audio_tag_enricher_missing_file_returns_none(tmp_path):
    enricher = AudioTagEnricher()
    track = _local_track(tmp_path / "nonexistent.flac")
    assert asyncio.run(enricher.enrich(track)) is None
