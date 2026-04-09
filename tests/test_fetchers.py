from dataclasses import replace
from pathlib import Path

import pytest

from lrx_cli.config import AppConfig, load_config
from lrx_cli.core import LrcManager
from lrx_cli.fetchers import FetcherMethodType
from lrx_cli.models import TrackMeta
from tests.marks import (
    requires_musixmatch_token,
    requires_qq_music,
    requires_spotify,
)

SAMPLE_SPOTIFY_TRACK: TrackMeta = TrackMeta(
    title="One Last Kiss",
    artist="Hikaru Utada",
    album="One Last Kiss",
    length=252026,
    trackid="5RhWszHMSKzb7KiXk4Ae0M",
    url="https://open.spotify.com/track/5RhWszHMSKzb7KiXk4Ae0M",
)

SAMPLE_SPOTIFY_TRACK_ALBUM_MODIFIED = replace(SAMPLE_SPOTIFY_TRACK, album="BADモード")

SAMPLE_SPOTIFY_TRACK_ARTIST_MODIFIED = replace(
    SAMPLE_SPOTIFY_TRACK, artist="宇多田ヒカル"
)

SAMPLE_SPOTIFY_TRACK_ALBUM_ARTIST_MODIFIED = replace(
    SAMPLE_SPOTIFY_TRACK, artist="宇多田ヒカル", album="BADモード"
)


@pytest.fixture
def lrc_manager(tmp_path: Path) -> LrcManager:
    """LrcManager with empty credentials (no auth required)."""
    return LrcManager(str(tmp_path / "cache.db"), AppConfig())


@pytest.fixture
def cred_lrc_manager(tmp_path: Path) -> LrcManager:
    """LrcManager with credentials from config.toml (for CI/network tests)."""
    return LrcManager(str(tmp_path / "cache.db"), load_config())


def _fetch_and_assert(
    lrc_manager: LrcManager,
    method: FetcherMethodType,
    expect_fail: bool = False,
    bypass_cache: bool = True,
) -> None:
    result = lrc_manager.fetch_for_track(
        SAMPLE_SPOTIFY_TRACK, force_method=method, bypass_cache=bypass_cache
    )
    if expect_fail:
        assert result is None
    else:
        assert result is not None
        assert result.status == "SUCCESS_SYNCED"
        assert result.lyrics is not None


def test_cache_search_fetcher_without_cache(lrc_manager: LrcManager):
    _fetch_and_assert(lrc_manager, "cache-search", expect_fail=True, bypass_cache=False)


@pytest.mark.parametrize(
    "query_track",
    [
        pytest.param(SAMPLE_SPOTIFY_TRACK, id="exact_match"),
        pytest.param(SAMPLE_SPOTIFY_TRACK_ARTIST_MODIFIED, id="artist_modified"),
        pytest.param(SAMPLE_SPOTIFY_TRACK_ALBUM_MODIFIED, id="album_modified"),
        pytest.param(
            SAMPLE_SPOTIFY_TRACK_ALBUM_ARTIST_MODIFIED, id="album_artist_modified"
        ),
    ],
)
def test_cache_search_fetcher_with_fuzzy_metadata(
    lrc_manager: LrcManager, query_track: TrackMeta
):
    expected_lrc = "[00:00.01]lyrics"
    lrc_manager.manual_insert(SAMPLE_SPOTIFY_TRACK, expected_lrc)

    result = lrc_manager.fetch_for_track(
        query_track, force_method="cache-search", bypass_cache=False
    )

    assert result is not None
    assert result.lyrics is not None
    assert result.lyrics.to_text() == expected_lrc


def test_cache_search_fetcher_prefer_better_match(lrc_manager: LrcManager):
    lrc_manager.manual_insert(
        SAMPLE_SPOTIFY_TRACK_ARTIST_MODIFIED, "[00:00.01]artist modified"
    )
    lrc_manager.manual_insert(
        SAMPLE_SPOTIFY_TRACK_ALBUM_ARTIST_MODIFIED, "[00:00.01]artist+album modified"
    )

    result = lrc_manager.fetch_for_track(
        SAMPLE_SPOTIFY_TRACK, force_method="cache-search", bypass_cache=False
    )

    assert result is not None
    assert result.lyrics is not None
    assert result.lyrics.to_text() == "[00:00.01]artist modified"


@pytest.mark.network
@pytest.mark.parametrize(
    "method, expect_fail",
    [
        ("lrclib", False),
        ("lrclib-search", False),
        ("netease", False),
        ("spotify", True),  # requires auth
        ("qqmusic", True),  # requires api
    ],
)
def test_anonymous_remote_fetchers(
    lrc_manager: LrcManager,
    method: FetcherMethodType,
    expect_fail: bool,
):
    _fetch_and_assert(lrc_manager, method, expect_fail)


@pytest.mark.network
@requires_spotify
def test_spotify_fetcher(cred_lrc_manager: LrcManager):
    _fetch_and_assert(cred_lrc_manager, "spotify")


@pytest.mark.network
@requires_qq_music
def test_qqmusic_fetcher(cred_lrc_manager: LrcManager):
    _fetch_and_assert(cred_lrc_manager, "qqmusic")


@pytest.mark.network
def test_musixmatch_anonymous_fetcher(lrc_manager: LrcManager):
    # These fetchers should be tested in a single test to share the same usertoken
    # Otherwise the second may fail due to rate limits
    _fetch_and_assert(lrc_manager, "musixmatch", expect_fail=False)
    _fetch_and_assert(lrc_manager, "musixmatch-spotify", expect_fail=False)


@pytest.mark.network
@requires_musixmatch_token
def test_musixmatch_fetcher(cred_lrc_manager: LrcManager):
    _fetch_and_assert(cred_lrc_manager, "musixmatch")
    _fetch_and_assert(cred_lrc_manager, "musixmatch-spotify")


def test_local_fetcher(lrc_manager: LrcManager):
    # Since this not a local track
    _fetch_and_assert(lrc_manager, "local", True)
