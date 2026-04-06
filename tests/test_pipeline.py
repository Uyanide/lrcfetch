from __future__ import annotations

import asyncio
from unittest.mock import patch

from lrx_cli.config import HIGH_CONFIDENCE
from lrx_cli.cache import SLOT_UNSYNCED
from lrx_cli.core import LrcManager
from lrx_cli.fetchers.base import BaseFetcher, FetchResult
from lrx_cli.lrc import LRCData
from lrx_cli.models import CacheStatus, LyricResult, TrackMeta


# Helpers


def _track(**kwargs) -> TrackMeta:
    defaults = dict(artist="Artist", title="Song", album="Album", length=180000)
    defaults.update(kwargs)
    return TrackMeta(**defaults)  # type: ignore


def _synced(source: str, confidence: float = HIGH_CONFIDENCE) -> LyricResult:
    return LyricResult(
        status=CacheStatus.SUCCESS_SYNCED,
        lyrics=LRCData("[00:01.00]lyrics"),
        source=source,
        confidence=confidence,
    )


def _unsynced(source: str, confidence: float = 60.0) -> LyricResult:
    return LyricResult(
        status=CacheStatus.SUCCESS_UNSYNCED,
        lyrics=LRCData("lyrics"),
        source=source,
        confidence=confidence,
    )


def _not_found() -> LyricResult:
    return LyricResult(status=CacheStatus.NOT_FOUND)


def _fr(
    synced: LyricResult | None = None,
    unsynced: LyricResult | None = None,
) -> FetchResult:
    return FetchResult(synced=synced, unsynced=unsynced)


class MockFetcher(BaseFetcher):
    def __init__(self, name: str, result: FetchResult, delay: float = 0.0):
        self._name = name
        self._result = result
        self._delay = delay
        self.called = False
        self.completed = False

    @property
    def source_name(self) -> str:
        return self._name

    def is_available(self, track: TrackMeta) -> bool:
        return True

    async def fetch(self, track: TrackMeta, bypass_cache: bool = False) -> FetchResult:
        self.called = True
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            self.completed = True
            return self._result
        except asyncio.CancelledError:
            raise


def make_manager(tmp_path) -> LrcManager:
    return LrcManager(db_path=str(tmp_path / "cache.db"))


# Between-group termination


def test_unsynced_does_not_stop_next_group(tmp_path):
    """Unsynced result should not stop the pipeline — next group must still run."""
    a = MockFetcher("a", _fr(unsynced=_unsynced("a")))
    b = MockFetcher("b", _fr(synced=_synced("b")))
    manager = make_manager(tmp_path)
    with patch("lrx_cli.core.build_plan", return_value=[[a], [b]]):
        result = manager.fetch_for_track(_track())
    assert b.called
    assert result is not None
    assert result.source == "b"


def test_trusted_synced_stops_next_group(tmp_path):
    """Trusted synced from group1 must prevent group2 from running."""
    a = MockFetcher("a", _fr(synced=_synced("a")))
    b = MockFetcher("b", _fr(synced=_synced("b")))
    manager = make_manager(tmp_path)
    with patch("lrx_cli.core.build_plan", return_value=[[a], [b]]):
        result = manager.fetch_for_track(_track())
    assert not b.called
    assert result is not None
    assert result.source == "a"


def test_negative_continues_next_group(tmp_path):
    """NOT_FOUND from group1 must cause group2 to be tried."""
    a = MockFetcher("a", _fr(synced=_not_found(), unsynced=_not_found()))
    b = MockFetcher("b", _fr(synced=_synced("b")))
    manager = make_manager(tmp_path)
    with patch("lrx_cli.core.build_plan", return_value=[[a], [b]]):
        result = manager.fetch_for_track(_track())
    assert a.called
    assert b.called
    assert result is not None
    assert result.source == "b"


# Within-group behaviour


def test_trusted_synced_cancels_sibling(tmp_path):
    """When a fast fetcher returns trusted synced, the slow sibling must be cancelled.
    If cancellation is broken this test will block for 10 seconds."""
    fast = MockFetcher("fast", _fr(synced=_synced("fast")))
    slow = MockFetcher("slow", _fr(synced=_synced("slow")), delay=10.0)
    manager = make_manager(tmp_path)
    with patch("lrx_cli.core.build_plan", return_value=[[fast, slow]]):
        result = manager.fetch_for_track(_track())
    assert fast.called
    assert slow.called  # task was started
    assert not slow.completed  # but cancelled before finishing
    assert result is not None
    assert result.source == "fast"


def test_allow_unsynced_true_picks_highest_confidence_unsynced(tmp_path):
    """When allow_unsynced=True and no trusted synced result, highest-confidence unsynced is returned."""
    low = MockFetcher("low", _fr(unsynced=_unsynced("low", confidence=40.0)))
    high = MockFetcher("high", _fr(unsynced=_unsynced("high", confidence=70.0)))
    manager = make_manager(tmp_path)
    with patch("lrx_cli.core.build_plan", return_value=[[low, high]]):
        result = manager.fetch_for_track(_track(), allow_unsynced=True)
    assert result is not None
    assert result.source == "high"


def test_equal_confidence_prefers_synced_when_unsynced_allowed(tmp_path):
    """Tie on confidence should still prefer synced over unsynced."""
    dual = MockFetcher(
        "dual",
        _fr(
            synced=_synced("dual", confidence=70.0),
            unsynced=_unsynced("dual", confidence=70.0),
        ),
    )
    manager = make_manager(tmp_path)
    with patch("lrx_cli.core.build_plan", return_value=[[dual]]):
        result = manager.fetch_for_track(_track(), allow_unsynced=True)
    assert result is not None
    assert result.status == CacheStatus.SUCCESS_SYNCED


def test_unsynced_only_returns_none_when_not_allowed(tmp_path):
    """When allow_unsynced=False, unsynced-only pipeline result must be rejected."""
    only_unsynced = MockFetcher(
        "u",
        _fr(unsynced=_unsynced("u", confidence=95.0)),
    )
    manager = make_manager(tmp_path)
    with patch("lrx_cli.core.build_plan", return_value=[[only_unsynced]]):
        result = manager.fetch_for_track(_track(), allow_unsynced=False)
    assert result is None


def test_allow_unsynced_flag_controls_return_type(tmp_path):
    """With both slots available, allow_unsynced controls whether unsynced can be returned."""
    dual = MockFetcher(
        "dual",
        _fr(
            synced=_synced("dual", confidence=55.0),
            unsynced=_unsynced("dual", confidence=95.0),
        ),
    )
    manager = make_manager(tmp_path)

    with patch("lrx_cli.core.build_plan", return_value=[[dual]]):
        synced_only = manager.fetch_for_track(_track(), allow_unsynced=False)
    assert synced_only is not None
    assert synced_only.status == CacheStatus.SUCCESS_SYNCED

    with patch("lrx_cli.core.build_plan", return_value=[[dual]]):
        allow_unsynced = manager.fetch_for_track(_track(), allow_unsynced=True)
    assert allow_unsynced is not None
    assert allow_unsynced.status == CacheStatus.SUCCESS_UNSYNCED


# Cache interaction


def test_cache_negative_skips_fetch(tmp_path):
    """A cached NOT_FOUND entry must prevent the fetcher from being called."""
    fetcher = MockFetcher("src", _fr(synced=_synced("src")))
    manager = make_manager(tmp_path)
    track = _track()
    manager.cache.set(track, "src", _not_found(), ttl_seconds=3600)
    with patch("lrx_cli.core.build_plan", return_value=[[fetcher]]):
        result = manager.fetch_for_track(track)
    assert not fetcher.called
    assert result is None


def test_cache_trusted_synced_no_fetch(tmp_path):
    """A trusted synced cache hit must be returned without calling the fetcher."""
    fetcher = MockFetcher("src", _fr())
    manager = make_manager(tmp_path)
    track = _track()
    manager.cache.set(track, "src", _synced("src"), ttl_seconds=3600)
    with patch("lrx_cli.core.build_plan", return_value=[[fetcher]]):
        result = manager.fetch_for_track(track)
    assert not fetcher.called
    assert result is not None
    assert result.status == CacheStatus.SUCCESS_SYNCED


def test_cached_slots_support_strategy_switch_without_refetch(
    tmp_path,
):
    """When both slots are cached, strategy switch should reuse cache without re-fetch."""
    fetcher = MockFetcher(
        "src",
        _fr(
            synced=_synced("src", confidence=85.0),
            unsynced=_unsynced("src", confidence=95.0),
        ),
    )
    manager = make_manager(tmp_path)
    track = _track()

    # First request: permissive strategy, unsynced wins and is cached.
    with patch("lrx_cli.core.build_plan", return_value=[[fetcher]]):
        first = manager.fetch_for_track(track, allow_unsynced=True)
    assert first is not None
    assert first.status == CacheStatus.SUCCESS_UNSYNCED

    fetcher.called = False

    # Second request: stricter strategy should use synced cache slot directly.
    with patch("lrx_cli.core.build_plan", return_value=[[fetcher]]):
        second = manager.fetch_for_track(track, allow_unsynced=False)

    assert not fetcher.called
    assert second is not None
    assert second.status == CacheStatus.SUCCESS_SYNCED


def test_unsynced_cache_only_still_fetches_when_unsynced_disallowed(tmp_path):
    """If only unsynced cache slot exists, allow_unsynced=False must still fetch synced."""
    fetcher = MockFetcher("src", _fr(synced=_synced("src", confidence=88.0)))
    manager = make_manager(tmp_path)
    track = _track()

    manager.cache.set(
        track,
        "src",
        _unsynced("src", confidence=95.0),
        ttl_seconds=3600,
        positive_kind=SLOT_UNSYNCED,
    )

    with patch("lrx_cli.core.build_plan", return_value=[[fetcher]]):
        result = manager.fetch_for_track(track, allow_unsynced=False)

    assert fetcher.called
    assert result is not None
    assert result.status == CacheStatus.SUCCESS_SYNCED
