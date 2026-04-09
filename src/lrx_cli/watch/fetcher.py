"""Debounced lyric fetch orchestration for watch session."""

import asyncio
from typing import Awaitable, Callable, Optional

from ..lrc import LRCData
from ..models import TrackMeta


class LyricFetcher:
    """Debounces track updates and runs at most one lyric fetch task at a time."""

    _watch_debounce_ms: int
    _fetch_func: Callable[[TrackMeta], Awaitable[Optional[LRCData]]]
    _on_fetching: Callable[[], Awaitable[None] | None]
    _on_result: Callable[[Optional[LRCData]], Awaitable[None] | None]
    _debounce_task: asyncio.Task | None
    _fetch_task: asyncio.Task | None
    _pending_track: TrackMeta | None

    def __init__(
        self,
        fetch_func: Callable[[TrackMeta], Awaitable[Optional[LRCData]]],
        on_fetching: Callable[[], Awaitable[None] | None],
        on_result: Callable[[Optional[LRCData]], Awaitable[None] | None],
        watch_debounce_ms: int,
    ) -> None:
        """Initialize fetch callbacks and runtime options."""
        self._watch_debounce_ms = watch_debounce_ms
        self._fetch_func = fetch_func
        self._on_fetching = on_fetching
        self._on_result = on_result
        self._debounce_task: asyncio.Task | None = None
        self._fetch_task: asyncio.Task | None = None
        self._pending_track: TrackMeta | None = None

    async def stop(self) -> None:
        """Cancel and await all in-flight debounce/fetch tasks."""
        for task in (self._debounce_task, self._fetch_task):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *[t for t in (self._debounce_task, self._fetch_task) if t is not None],
            return_exceptions=True,
        )
        self._debounce_task = None
        self._fetch_task = None

    def request(self, track: TrackMeta) -> None:
        """Request lyrics for track with debounce collapsing."""
        self._pending_track = track
        if self._debounce_task is not None:
            self._debounce_task.cancel()
        self._debounce_task = asyncio.create_task(self._debounce_then_fetch())

    async def _debounce_then_fetch(self) -> None:
        """Wait debounce window then start a fresh fetch task for latest pending track."""
        await asyncio.sleep(self._watch_debounce_ms / 1000.0)
        track = self._pending_track
        if track is None:
            return

        if self._fetch_task is not None:
            self._fetch_task.cancel()
            await asyncio.gather(self._fetch_task, return_exceptions=True)

        self._fetch_task = asyncio.create_task(self._do_fetch(track))

    async def _do_fetch(self, track: TrackMeta) -> None:
        """Execute fetch lifecycle callbacks and fetch lyrics for a track."""
        fetching_callback_result = self._on_fetching()
        if asyncio.iscoroutine(fetching_callback_result):
            await fetching_callback_result

        lyrics = await self._fetch_func(track)

        result_callback_result = self._on_result(lyrics)
        if asyncio.iscoroutine(result_callback_result):
            await result_callback_result
