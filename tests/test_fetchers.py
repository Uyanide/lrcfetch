from dataclasses import replace
import asyncio
import json
from pathlib import Path
from typing import Callable

import httpx
import pytest

from lrx_cli.authenticators import create_authenticators
from lrx_cli.cache import CacheEngine
from lrx_cli.config import AppConfig, load_config
from lrx_cli.core import LrcManager
from lrx_cli.fetchers import FetcherMethodType, create_fetchers
from lrx_cli.fetchers.lrclib import LrclibFetcher, _parse_lrclib_response
from lrx_cli.fetchers.lrclib_search import (
    LrclibSearchFetcher,
    _parse_lrclib_search_results,
)
from lrx_cli.fetchers.musixmatch import (
    MusixmatchFetcher,
    MusixmatchSpotifyFetcher,
    _parse_mxm_macro,
    _parse_mxm_search,
)
from lrx_cli.fetchers.netease import (
    NeteaseFetcher,
    _parse_netease_lyrics,
    _parse_netease_search,
)
from lrx_cli.fetchers.qqmusic import QQMusicFetcher, _parse_qq_lyrics, _parse_qq_search
from lrx_cli.fetchers.spotify import SpotifyFetcher, _parse_spotify_lyrics
from lrx_cli.lrc import LRCData
from lrx_cli.models import CacheStatus, TrackMeta
from tests.marks import requires_musixmatch_token, requires_qq_music, requires_spotify

SAMPLE_TRACK = TrackMeta(
    title="One Last Kiss",
    artist="Hikaru Utada",
    album="One Last Kiss",
    length=252026,
    trackid="5RhWszHMSKzb7KiXk4Ae0M",
    url="https://open.spotify.com/track/5RhWszHMSKzb7KiXk4Ae0M",
)

SAMPLE_TRACK_ALBUM_MODIFIED = replace(SAMPLE_TRACK, album="BADモード")
SAMPLE_TRACK_ARTIST_MODIFIED = replace(SAMPLE_TRACK, artist="宇多田ヒカル")
SAMPLE_TRACK_ALBUM_ARTIST_MODIFIED = replace(
    SAMPLE_TRACK,
    artist="宇多田ヒカル",
    album="BADモード",
)

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "fetchers"
_NETWORK_TIMEOUT = 20.0

ParserFunc = Callable[[dict], LRCData | None]


@pytest.fixture
def lrc_manager(tmp_path: Path) -> LrcManager:
    return LrcManager(str(tmp_path / "cache.db"), AppConfig())


@pytest.fixture
def cred_lrc_manager(tmp_path: Path) -> LrcManager:
    return LrcManager(str(tmp_path / "cache.db"), load_config())


@pytest.fixture
def fetcher_runtime_anonymous(tmp_path: Path):
    cfg = AppConfig()
    cache = CacheEngine(str(tmp_path / "network-anon-cache.db"))
    authenticators = create_authenticators(cache, cfg)
    fetchers = create_fetchers(cache, authenticators, cfg)
    return fetchers, cfg


@pytest.fixture
def fetcher_runtime_credentialed(tmp_path: Path):
    cfg = load_config()
    cache = CacheEngine(str(tmp_path / "network-cred-cache.db"))
    authenticators = create_authenticators(cache, cfg)
    fetchers = create_fetchers(cache, authenticators, cfg)
    return fetchers, cfg


def _load_fixture(name: str) -> dict | list:
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _assert_shape(actual: object, fixture: object) -> None:
    """Assert actual payload contains fixture structure recursively.

    - dict: all fixture keys must exist with matching nested shape
    - list: actual must contain at least fixture length and each indexed shape must match
    - scalar: runtime type must match fixture type
    """
    if isinstance(fixture, dict):
        assert isinstance(actual, dict)
        for key, value in fixture.items():
            assert key in actual
            _assert_shape(actual[key], value)
        return

    if isinstance(fixture, list):
        assert isinstance(actual, list)
        assert len(actual) >= len(fixture)
        for idx, value in enumerate(fixture):
            _assert_shape(actual[idx], value)
        return

    if fixture is None:
        return

    assert isinstance(actual, type(fixture))


def _fetch_with_method(
    lrc_manager: LrcManager,
    method: FetcherMethodType,
    *,
    bypass_cache: bool = False,
):
    return lrc_manager.fetch_for_track(
        SAMPLE_TRACK,
        force_method=method,
        bypass_cache=bypass_cache,
    )


# Cache-search fetcher behavior


def test_cache_search_no_cache_fails(lrc_manager: LrcManager):
    result = _fetch_with_method(lrc_manager, "cache-search", bypass_cache=False)
    assert result is None


def test_cache_search_exact_hit(lrc_manager: LrcManager):
    expected = "[00:00.01]lyrics"
    lrc_manager.manual_insert(SAMPLE_TRACK, expected)

    result = lrc_manager.fetch_for_track(
        SAMPLE_TRACK,
        force_method="cache-search",
        bypass_cache=False,
    )

    assert result is not None
    assert result.lyrics is not None
    assert result.lyrics.to_text() == expected


@pytest.mark.parametrize(
    "query_track",
    [
        pytest.param(SAMPLE_TRACK_ARTIST_MODIFIED, id="artist_modified"),
        pytest.param(SAMPLE_TRACK_ALBUM_MODIFIED, id="album_modified"),
        pytest.param(SAMPLE_TRACK_ALBUM_ARTIST_MODIFIED, id="album_artist_modified"),
    ],
)
def test_cache_search_fuzzy_hit(lrc_manager: LrcManager, query_track: TrackMeta):
    expected = "[00:00.01]lyrics"
    lrc_manager.manual_insert(SAMPLE_TRACK, expected)

    result = lrc_manager.fetch_for_track(
        query_track,
        force_method="cache-search",
        bypass_cache=False,
    )

    assert result is not None
    assert result.lyrics is not None
    assert result.lyrics.to_text() == expected


def test_cache_search_prefer_better_match(lrc_manager: LrcManager):
    lrc_manager.manual_insert(
        SAMPLE_TRACK_ARTIST_MODIFIED,
        "[00:00.01]artist modified",
    )
    lrc_manager.manual_insert(
        SAMPLE_TRACK_ALBUM_ARTIST_MODIFIED,
        "[00:00.01]artist+album modified",
    )

    result = lrc_manager.fetch_for_track(
        SAMPLE_TRACK,
        force_method="cache-search",
        bypass_cache=False,
    )

    assert result is not None
    assert result.lyrics is not None
    assert result.lyrics.to_text() == "[00:00.01]artist modified"


# API response format for every fetcher


@pytest.mark.network
def test_api_lrclib_response_shape(fetcher_runtime_anonymous):
    fetchers, _cfg = fetcher_runtime_anonymous
    fetcher = fetchers["lrclib"]
    assert isinstance(fetcher, LrclibFetcher)

    async def _run() -> dict:
        async with httpx.AsyncClient(timeout=_NETWORK_TIMEOUT) as client:
            response = await fetcher._api_get(client, SAMPLE_TRACK)
            assert response.status_code == 200
            payload = response.json()
            assert isinstance(payload, dict)
            return payload

    payload = asyncio.run(_run())
    _assert_shape(payload, _load_fixture("lrclib_response.json"))


@pytest.mark.network
def test_api_lrclib_search_response_shape(fetcher_runtime_anonymous):
    fetchers, _cfg = fetcher_runtime_anonymous
    fetcher = fetchers["lrclib-search"]
    assert isinstance(fetcher, LrclibSearchFetcher)

    async def _run() -> list[dict]:
        async with httpx.AsyncClient(timeout=_NETWORK_TIMEOUT) as client:
            items, had_error = await fetcher._api_candidates(client, SAMPLE_TRACK)
            assert had_error is False
            return items

    payload = asyncio.run(_run())
    _assert_shape(payload, _load_fixture("lrclib_search_results.json"))


@pytest.mark.network
def test_api_netease_response_shape(fetcher_runtime_anonymous):
    fetchers, _cfg = fetcher_runtime_anonymous
    fetcher = fetchers["netease"]
    assert isinstance(fetcher, NeteaseFetcher)

    async def _run() -> tuple[dict, dict]:
        async with httpx.AsyncClient(timeout=_NETWORK_TIMEOUT) as client:
            search = await fetcher._api_search_track(client, SAMPLE_TRACK, 5)
            lyric = await fetcher._api_lyric_track(client, SAMPLE_TRACK, 5)
            assert isinstance(search, dict)
            assert isinstance(lyric, dict)
            return search, lyric

    search_payload, lyric_payload = asyncio.run(_run())
    _assert_shape(search_payload, _load_fixture("netease_search.json"))
    _assert_shape(lyric_payload, _load_fixture("netease_lyrics.json"))


@pytest.mark.network
@requires_spotify
def test_api_spotify_response_shape(fetcher_runtime_credentialed):
    fetchers, _cfg = fetcher_runtime_credentialed
    fetcher = fetchers["spotify"]
    assert isinstance(fetcher, SpotifyFetcher)

    async def _run() -> dict:
        payload = await fetcher._api_lyrics(SAMPLE_TRACK)
        assert isinstance(payload, dict)
        return payload

    payload = asyncio.run(_run())
    _assert_shape(payload, _load_fixture("spotify_synced.json"))


@pytest.mark.network
@requires_qq_music
def test_api_qqmusic_response_shape(fetcher_runtime_credentialed):
    fetchers, _cfg = fetcher_runtime_credentialed
    fetcher = fetchers["qqmusic"]
    assert isinstance(fetcher, QQMusicFetcher)

    async def _run() -> tuple[dict, dict]:
        search = await fetcher._api_search(SAMPLE_TRACK, 10)
        lyric = await fetcher._api_lyric_track(SAMPLE_TRACK, 10)
        assert isinstance(search, dict)
        assert isinstance(lyric, dict)
        return search, lyric

    search_payload, lyric_payload = asyncio.run(_run())
    _assert_shape(search_payload, _load_fixture("qq_search.json"))
    _assert_shape(lyric_payload, _load_fixture("qq_lyrics.json"))


@pytest.mark.network
def test_api_musixmatch_anonymous_response_shape(fetcher_runtime_anonymous):
    """Anonymous musixmatch calls must share one cache/auth context in this test."""
    fetchers, _cfg = fetcher_runtime_anonymous
    search_fetcher = fetchers["musixmatch"]
    spotify_fetcher = fetchers["musixmatch-spotify"]
    assert isinstance(search_fetcher, MusixmatchFetcher)
    assert isinstance(spotify_fetcher, MusixmatchSpotifyFetcher)

    async def _run() -> tuple[dict, dict, dict]:
        search = await search_fetcher._api_search_track(SAMPLE_TRACK)
        macro_from_search = await search_fetcher._api_macro_track(SAMPLE_TRACK)
        macro_from_spotify = await spotify_fetcher._api_macro_track(SAMPLE_TRACK)
        assert isinstance(search, dict)
        assert isinstance(macro_from_search, dict)
        assert isinstance(macro_from_spotify, dict)
        return search, macro_from_search, macro_from_spotify

    search_payload, macro_payload, spotify_macro_payload = asyncio.run(_run())
    _assert_shape(search_payload, _load_fixture("musixmatch_search.json"))
    _assert_shape(macro_payload, _load_fixture("musixmatch_macro_richsync.json"))
    _assert_shape(
        spotify_macro_payload, _load_fixture("musixmatch_macro_richsync.json")
    )


@pytest.mark.network
@requires_musixmatch_token
def test_api_musixmatch_token_response_shape(fetcher_runtime_credentialed):
    fetchers, _cfg = fetcher_runtime_credentialed
    search_fetcher = fetchers["musixmatch"]
    spotify_fetcher = fetchers["musixmatch-spotify"]
    assert isinstance(search_fetcher, MusixmatchFetcher)
    assert isinstance(spotify_fetcher, MusixmatchSpotifyFetcher)

    async def _run() -> tuple[dict, dict, dict]:
        search = await search_fetcher._api_search_track(SAMPLE_TRACK)
        macro_from_search = await search_fetcher._api_macro_track(SAMPLE_TRACK)
        macro_from_spotify = await spotify_fetcher._api_macro_track(SAMPLE_TRACK)
        assert isinstance(search, dict)
        assert isinstance(macro_from_search, dict)
        assert isinstance(macro_from_spotify, dict)
        return search, macro_from_search, macro_from_spotify

    search_payload, macro_payload, spotify_macro_payload = asyncio.run(_run())
    _assert_shape(search_payload, _load_fixture("musixmatch_search.json"))
    _assert_shape(macro_payload, _load_fixture("musixmatch_macro_richsync.json"))
    _assert_shape(
        spotify_macro_payload, _load_fixture("musixmatch_macro_richsync.json")
    )


# Parse fixture JSON into real data structures


@pytest.mark.parametrize(
    "fixture_name,parser,expected_status",
    [
        pytest.param(
            "spotify_synced.json",
            _parse_spotify_lyrics,
            "SUCCESS_SYNCED",
            id="spotify-synced",
        ),
        pytest.param(
            "spotify_unsynced.json",
            _parse_spotify_lyrics,
            "SUCCESS_UNSYNCED",
            id="spotify-unsynced",
        ),
    ],
)
def test_parse_spotify_fixture(
    fixture_name: str,
    parser: ParserFunc,
    expected_status: str,
):
    payload = _load_fixture(fixture_name)
    assert isinstance(payload, dict)
    parsed = parser(payload)
    assert parsed is not None
    assert parsed.detect_sync_status().value == expected_status
    if expected_status == "SUCCESS_SYNCED":
        assert parsed.to_text() == "[00:01.00]hello\n[00:02.50]world"
    else:
        assert parsed.to_text() == "[00:00.00]plain one\n[00:00.00]plain two"


def test_parse_qq_search_fixture() -> None:
    payload = _load_fixture("qq_search.json")
    assert isinstance(payload, dict)
    parsed = _parse_qq_search(payload)
    assert len(parsed) == 2

    assert parsed[0].item == "mid1"
    assert parsed[0].title == "My Love"
    assert parsed[0].artist == "Westlife"
    assert parsed[0].duration_ms == 232000.0
    assert parsed[0].album == "Coast To Coast"

    assert parsed[1].item == "mid2"
    assert parsed[1].title == "My Love (Album Version)"
    assert parsed[1].artist == "Little Texas"
    assert parsed[1].duration_ms == 248000.0
    assert parsed[1].album == "Greatest Hits"


def test_parse_qq_lyrics_fixture() -> None:
    payload = _load_fixture("qq_lyrics.json")
    assert isinstance(payload, dict)
    parsed = _parse_qq_lyrics(payload)
    assert parsed is not None
    assert len(parsed) == 2
    assert parsed.detect_sync_status() == CacheStatus.SUCCESS_SYNCED


def test_parse_lrclib_response_fixture() -> None:
    payload = _load_fixture("lrclib_response.json")
    assert isinstance(payload, dict)
    parsed = _parse_lrclib_response(payload)
    assert parsed.synced is not None and parsed.synced.lyrics is not None
    assert parsed.unsynced is not None and parsed.unsynced.lyrics is not None
    assert parsed.synced.status == CacheStatus.SUCCESS_SYNCED
    assert parsed.unsynced.status == CacheStatus.SUCCESS_UNSYNCED
    assert parsed.synced.lyrics.to_text() == "[00:01.00]s1\n[00:02.00]s2"
    assert parsed.unsynced.lyrics.to_text() == "[00:00.00]p1\n[00:00.00]p2"


def test_parse_lrclib_search_results_fixture() -> None:
    payload = _load_fixture("lrclib_search_results.json")
    assert isinstance(payload, list)
    parsed = _parse_lrclib_search_results(payload)
    assert len(parsed) == 2

    assert parsed[0].item.get("id") == 1
    assert parsed[0].duration_ms == 231847.0
    assert parsed[0].is_synced is True
    assert parsed[0].title == "My Love"
    assert parsed[0].artist == "Westlife"
    assert parsed[0].album == "Coast To Coast"

    assert parsed[1].item.get("id") == 2
    assert parsed[1].duration_ms == 262000.0
    assert parsed[1].is_synced is False
    assert parsed[1].title == "My Love (Live)"
    assert parsed[1].artist == "Westlife"
    assert parsed[1].album == "Live"


def test_parse_netease_search_fixture() -> None:
    payload = _load_fixture("netease_search.json")
    assert isinstance(payload, dict)
    parsed = _parse_netease_search(payload)
    assert len(parsed) == 2
    assert parsed[0].item == 2080607
    assert parsed[0].title == "My Love"
    assert parsed[0].artist == "Westlife"
    assert parsed[0].duration_ms == 231941.0
    assert parsed[0].album == "Unbreakable"

    assert parsed[1].item == 572412968
    assert parsed[1].artist == "Westlife"
    assert parsed[1].duration_ms == 231000.0


def test_parse_netease_lyrics_fixture() -> None:
    payload = _load_fixture("netease_lyrics.json")
    assert isinstance(payload, dict)
    parsed = _parse_netease_lyrics(payload)
    assert parsed is not None
    assert len(parsed) == 2
    assert parsed.detect_sync_status() == CacheStatus.SUCCESS_SYNCED
    assert parsed.to_text() == "[00:01.00]line1\n[00:02.00]line2"


def test_parse_musixmatch_search_fixture() -> None:
    payload = _load_fixture("musixmatch_search.json")
    assert isinstance(payload, dict)
    parsed = _parse_mxm_search(payload)
    assert len(parsed) == 1
    assert parsed[0].item == 123
    assert parsed[0].is_synced is True
    assert parsed[0].title == "My Love"
    assert parsed[0].artist == "Westlife"
    assert parsed[0].duration_ms == 232000.0
    assert parsed[0].album == "Coast To Coast"


def test_parse_musixmatch_macro_fixture() -> None:
    payload = _load_fixture("musixmatch_macro_richsync.json")
    assert isinstance(payload, dict)
    parsed = _parse_mxm_macro(payload)
    assert parsed is not None
    assert len(parsed) == 2
    assert parsed.detect_sync_status() == CacheStatus.SUCCESS_SYNCED


def test_parse_musixmatch_macro_subtitle_fallback_fixture() -> None:
    payload = _load_fixture("musixmatch_macro_subtitle.json")
    assert isinstance(payload, dict)
    parsed = _parse_mxm_macro(payload)
    assert parsed is not None
    assert len(parsed) == 2
    assert parsed.detect_sync_status() == CacheStatus.SUCCESS_SYNCED
    assert parsed.to_text() == "[00:01.10]hello\n[00:02.22]world"


# Empty / partial-error response handling


def test_parse_spotify_empty_or_invalid() -> None:
    assert _parse_spotify_lyrics({}) is None
    assert _parse_spotify_lyrics({"lyrics": {"lines": []}}) is None


def test_parse_qq_search_empty_or_error() -> None:
    assert _parse_qq_search({}) == []
    assert _parse_qq_search({"code": 1}) == []
    assert _parse_qq_search({"code": 0, "data": {"list": []}}) == []


def test_parse_qq_lyrics_empty_or_error() -> None:
    assert _parse_qq_lyrics({}) is None
    assert _parse_qq_lyrics({"code": 1}) is None
    assert _parse_qq_lyrics({"code": 0, "data": {"lyric": ""}}) is None


def test_parse_lrclib_response_empty_or_partial() -> None:
    parsed = _parse_lrclib_response({})
    assert parsed.synced is not None
    assert parsed.unsynced is not None
    assert parsed.synced.lyrics is None
    assert parsed.unsynced.lyrics is None

    parsed_partial = _parse_lrclib_response({"syncedLyrics": "[00:01.00]line"})
    assert (
        parsed_partial.synced is not None and parsed_partial.synced.lyrics is not None
    )
    assert parsed_partial.unsynced is not None


def test_parse_netease_empty_or_partial() -> None:
    assert _parse_netease_search({}) == []
    assert _parse_netease_search({"result": {"songs": []}}) == []
    assert _parse_netease_lyrics({}) is None
    assert _parse_netease_lyrics({"lrc": {"lyric": ""}}) is None


def test_parse_musixmatch_empty_or_partial() -> None:
    assert _parse_mxm_search({}) == []
    assert _parse_mxm_search({"message": {"body": {"track_list": []}}}) == []
    assert _parse_mxm_macro({}) is None
    assert _parse_mxm_macro({"message": {"body": []}}) is None
