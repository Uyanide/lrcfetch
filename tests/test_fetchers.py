from pathlib import Path
import pytest
from dataclasses import replace

from lrx_cli.fetchers import FetcherMethodType
from lrx_cli.models import TrackMeta
from lrx_cli.core import LrcManager

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
    return LrcManager(str(tmp_path / "cache.db"))


def _fetch_and_assert(
    lrc_manager: LrcManager, method: FetcherMethodType, expect_fail: bool = False
) -> None:
    result = lrc_manager.fetch_for_track(SAMPLE_SPOTIFY_TRACK, force_method=method)
    if expect_fail:
        assert result is None
    else:
        assert result is not None
        assert result.lyrics is not None


def test_cache_search_fetcher_without_cache(lrc_manager: LrcManager):
    _fetch_and_assert(lrc_manager, "cache-search", expect_fail=True)


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

    result = lrc_manager.fetch_for_track(query_track, force_method="cache-search")

    assert result is not None
    assert result.lyrics is not None
    assert result.lyrics.to_lrc() == expected_lrc


def test_cache_search_fetcher_prefer_better_match(lrc_manager: LrcManager):
    lrc_manager.manual_insert(
        SAMPLE_SPOTIFY_TRACK_ARTIST_MODIFIED, "[00:00.01]artist modified"
    )
    lrc_manager.manual_insert(
        SAMPLE_SPOTIFY_TRACK_ALBUM_ARTIST_MODIFIED, "[00:00.01]artist+album modified"
    )

    result = lrc_manager.fetch_for_track(
        SAMPLE_SPOTIFY_TRACK, force_method="cache-search"
    )

    assert result is not None
    assert result.lyrics is not None
    assert result.lyrics.to_lrc() == "[00:00.01]artist modified"


@pytest.mark.network
@pytest.mark.parametrize(
    "method, expect_fail",
    [
        ("lrclib", False),
        ("lrclib-search", False),
        ("netease", False),
    ],
)
def test_anonymous_remote_fetchers(
    lrc_manager: LrcManager, method: FetcherMethodType, expect_fail: bool
):
    _fetch_and_assert(lrc_manager, method, expect_fail)


def test_local_fetcher(lrc_manager: LrcManager):
    _fetch_and_assert(lrc_manager, "local", True)
