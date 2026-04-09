"""Playback position tracking utilities for watch mode."""

import asyncio
import time
from typing import Awaitable, Callable, Optional

from .options import WatchOptions


class PositionTracker:
    """Maintains an estimated playback position from seek/status events plus local clock."""

    _options: WatchOptions
    _poll_position_ms: Callable[[str], Awaitable[Optional[int]]]
    _active_player: str | None
    _is_playing: bool
    _track_key: str | None
    _position_ms: int
    _last_tick: float
    _fast_task: asyncio.Task | None
    _on_tick: Callable[[], None] | None
    _lock: asyncio.Lock

    def __init__(
        self,
        poll_position_ms: Callable[[str], Awaitable[Optional[int]]],
        options: WatchOptions,
        on_tick: Callable[[], None] | None = None,
    ) -> None:
        """Initialize tracker with position polling callback and runtime options."""
        self._options = options
        self._poll_position_ms = poll_position_ms
        self._on_tick = on_tick
        self._active_player: str | None = None
        self._is_playing = False
        self._track_key: str | None = None
        self._position_ms = 0
        self._last_tick = time.monotonic()
        self._fast_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start local monotonic position ticking task."""
        self._last_tick = time.monotonic()
        self._fast_task = asyncio.create_task(self._fast_loop())

    async def stop(self) -> None:
        """Stop tracker tasks and await clean cancellation."""
        tasks = [t for t in (self._fast_task,) if t is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._fast_task = None

    async def set_active_player(
        self,
        bus_name: str | None,
        playback_status: str,
        track_key: str | None,
    ) -> None:
        """Switch active source and calibrate position once when entering a new playing track."""
        should_calibrate_now = False
        async with self._lock:
            player_changed = self._active_player != bus_name
            track_changed = self._track_key != track_key
            was_playing = self._is_playing
            self._active_player = bus_name
            self._is_playing = playback_status == "Playing"
            status_changed_to_playing = self._is_playing and not was_playing
            if player_changed or track_changed:
                self._position_ms = 0
            should_calibrate_now = (
                self._is_playing
                and bool(self._active_player)
                and (player_changed or track_changed or status_changed_to_playing)
            )
            self._track_key = track_key
            self._last_tick = time.monotonic()

        if should_calibrate_now and self._active_player:
            await self._calibrate_once(self._active_player)

    async def on_seeked(self, bus_name: str, position_ms: int) -> None:
        """Apply explicit seek position update for active player."""
        async with self._lock:
            if bus_name != self._active_player:
                return
            self._position_ms = max(0, position_ms)
            self._last_tick = time.monotonic()

    async def on_playback_status(self, bus_name: str, playback_status: str) -> None:
        """Update playing state and calibrate once on paused-to-playing transition."""
        should_calibrate_now = False
        async with self._lock:
            if bus_name != self._active_player:
                return
            was_playing = self._is_playing
            self._is_playing = playback_status == "Playing"
            should_calibrate_now = self._is_playing and not was_playing
            self._last_tick = time.monotonic()

        if should_calibrate_now:
            await self._calibrate_once(bus_name)

    async def _fast_loop(self) -> None:
        """Advance position by monotonic clock while active player is playing."""
        interval = self._options.position_tick_ms / 1000.0
        while True:
            await asyncio.sleep(interval)
            should_notify = False
            async with self._lock:
                now = time.monotonic()
                if self._is_playing and self._active_player:
                    delta_ms = int((now - self._last_tick) * 1000)
                    if delta_ms > 0:
                        self._position_ms += delta_ms
                        should_notify = True
                self._last_tick = now

            if should_notify and self._on_tick is not None:
                self._on_tick()

    async def _calibrate_once(self, bus_name: str) -> None:
        """Poll player-reported position once and synchronize local tracker state."""
        polled = await self._poll_position_ms(bus_name)
        if polled is None:
            return
        async with self._lock:
            if bus_name != self._active_player:
                return
            # Drift correction is signal-assisted; polling is fallback.
            self._position_ms = max(0, polled)
            self._last_tick = time.monotonic()

    async def get_position_ms(self) -> int:
        """Return current tracked position in milliseconds."""
        async with self._lock:
            return max(0, int(self._position_ms))

    def peek_position_ms(self) -> int:
        """Return current tracked position without awaiting lock (best-effort snapshot)."""
        return max(0, int(self._position_ms))
