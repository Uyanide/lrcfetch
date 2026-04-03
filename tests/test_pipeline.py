from __future__ import annotations

import asyncio
from unittest.mock import patch

from lrx_cli.config import HIGH_CONFIDENCE
from lrx_cli.core import LrcManager
from lrx_cli.fetchers.base import BaseFetcher
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


class MockFetcher(BaseFetcher):
    def __init__(self, name: str, result: LyricResult | None, delay: float = 0.0):
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

    async def fetch(
        self, track: TrackMeta, bypass_cache: bool = False
    ) -> LyricResult | None:
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
    a = MockFetcher("a", _unsynced("a"))
    b = MockFetcher("b", _synced("b"))
    manager = make_manager(tmp_path)
    with patch("lrx_cli.core.build_plan", return_value=[[a], [b]]):
        result = manager.fetch_for_track(_track())
    assert b.called
    assert result is not None
    assert result.source == "b"


def test_trusted_synced_stops_next_group(tmp_path):
    """Trusted synced from group1 must prevent group2 from running."""
    a = MockFetcher("a", _synced("a"))
    b = MockFetcher("b", _synced("b"))
    manager = make_manager(tmp_path)
    with patch("lrx_cli.core.build_plan", return_value=[[a], [b]]):
        result = manager.fetch_for_track(_track())
    assert not b.called
    assert result is not None
    assert result.source == "a"


def test_negative_continues_next_group(tmp_path):
    """NOT_FOUND from group1 must cause group2 to be tried."""
    a = MockFetcher("a", _not_found())
    b = MockFetcher("b", _synced("b"))
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
    fast = MockFetcher("fast", _synced("fast"))
    slow = MockFetcher("slow", _synced("slow"), delay=10.0)
    manager = make_manager(tmp_path)
    with patch("lrx_cli.core.build_plan", return_value=[[fast, slow]]):
        result = manager.fetch_for_track(_track())
    assert fast.called
    assert slow.called  # task was started
    assert not slow.completed  # but cancelled before finishing
    assert result is not None
    assert result.source == "fast"


def test_best_confidence_within_group(tmp_path):
    """When no trusted synced result, highest-confidence result from group is returned."""
    low = MockFetcher("low", _unsynced("low", confidence=40.0))
    high = MockFetcher("high", _unsynced("high", confidence=70.0))
    manager = make_manager(tmp_path)
    with patch("lrx_cli.core.build_plan", return_value=[[low, high]]):
        result = manager.fetch_for_track(_track())
    assert result is not None
    assert result.source == "high"


# Cache interaction


def test_cache_negative_skips_fetch(tmp_path):
    """A cached NOT_FOUND entry must prevent the fetcher from being called."""
    fetcher = MockFetcher("src", _synced("src"))
    manager = make_manager(tmp_path)
    track = _track()
    manager.cache.set(track, "src", _not_found(), ttl_seconds=3600)
    with patch("lrx_cli.core.build_plan", return_value=[[fetcher]]):
        result = manager.fetch_for_track(track)
    assert not fetcher.called
    assert result is None


def test_cache_trusted_synced_no_fetch(tmp_path):
    """A trusted synced cache hit must be returned without calling the fetcher."""
    fetcher = MockFetcher("src", None)
    manager = make_manager(tmp_path)
    track = _track()
    manager.cache.set(track, "src", _synced("src"), ttl_seconds=3600)
    with patch("lrx_cli.core.build_plan", return_value=[[fetcher]]):
        result = manager.fetch_for_track(track)
    assert not fetcher.called
    assert result is not None
    assert result.status == CacheStatus.SUCCESS_SYNCED
