"""Watch orchestration with explicit MVVM role boundaries.

- Model: WatchModel stores domain state.
- ViewModel: WatchViewModel projects model to output-facing state/signature.
- Coordinator: WatchCoordinator wires services and drives async workflows.
"""

import asyncio
from dataclasses import asdict
from typing import Optional

from loguru import logger

from ..core import LrcManager
from ..lrc import LRCData
from ..models import TrackMeta
from .control import ControlServer
from .fetcher import LyricFetcher
from ..config import AppConfig
from .view import BaseOutput, LyricView, WatchState
from .player import ActivePlayerSelector, PlayerMonitor, PlayerTarget
from .tracker import PositionTracker


class WatchModel:
    """Model layer that owns watch state and lyric timeline representation."""

    offset_ms: int
    active_player: str | None
    active_track_key: str | None
    status: str
    lyrics: LyricView | None

    def __init__(self) -> None:
        self.offset_ms = 0
        self.active_player: str | None = None
        self.active_track_key: str | None = None
        self.status: str = "idle"
        self.lyrics: LyricView | None = None

    def set_lyrics(self, lyrics: LRCData | None) -> None:
        """Update lyrics and rebuild projection once per lyric object change."""
        if lyrics is None:
            self.lyrics = None
            return

        self.lyrics = LyricView.from_lrc(lyrics)

    def state_signature(self, track: TrackMeta | None, position_ms: int) -> tuple:
        """Build dedupe signature from model state and current lyric cursor."""
        track_key = (
            track.trackid
            if track and track.trackid
            else track.display_name()
            if track
            else None
        )

        if self.status != "ok" or self.lyrics is None:
            return ("status", self.status, self.active_player, track_key)
        at_ms = position_ms + self.offset_ms
        cursor = self.lyrics.signature_cursor(at_ms)
        return ("lyrics", self.active_player, track_key, cursor)


class WatchViewModel:
    """ViewModel that projects WatchModel into view-consumable snapshots."""

    _model: WatchModel

    def __init__(self, model: WatchModel) -> None:
        self._model = model

    def signature(self, track: TrackMeta | None, position_ms: int) -> tuple:
        """Build dedupe signature for current projected state."""
        return self._model.state_signature(track, position_ms)

    def state(self, track: TrackMeta | None, position_ms: int) -> WatchState:
        """Project model values into immutable WatchState payload."""
        return WatchState(
            track=track,
            lyrics=self._model.lyrics,
            position_ms=position_ms,
            offset_ms=self._model.offset_ms,
            status=self._model.status,  # type: ignore[arg-type]
        )


class WatchCoordinator:
    """Application/service orchestration layer for watch runtime."""

    _manager: LrcManager
    _output: BaseOutput
    _config: AppConfig
    _model: WatchModel
    _view_model: WatchViewModel
    _player_hint: str | None
    _last_emit_signature: tuple | None
    _target: PlayerTarget
    _control: ControlServer
    _player_monitor: PlayerMonitor
    _tracker: PositionTracker
    _fetcher: LyricFetcher
    _emit_scheduled: bool
    _calibration_task: asyncio.Task | None

    def __init__(
        self,
        manager: LrcManager,
        output: BaseOutput,
        player_hint: str | None,
        config: AppConfig,
    ) -> None:
        self._manager = manager
        self._output = output
        self._config = config
        self._model = WatchModel()
        self._view_model = WatchViewModel(self._model)
        self._player_hint = player_hint
        self._last_emit_signature: tuple | None = None
        self._emit_scheduled = False
        self._calibration_task = None

        self._target = PlayerTarget(
            hint=player_hint,
            player_blacklist=self._config.general.player_blacklist,
        )

        self._control = ControlServer(config=self._config)
        self._player_monitor = PlayerMonitor(
            on_players_changed=self._on_player_change,
            on_seeked=self._on_seeked,
            on_playback_status=self._on_playback_status,
            config=self._config,
            target=self._target,
        )
        self._tracker = PositionTracker(
            poll_position_ms=self._player_monitor.get_position_ms,
            config=self._config,
            on_tick=self._on_tracker_tick,
        )
        self._fetcher = LyricFetcher(
            fetch_func=self._fetch_lyrics,
            on_fetching=self._on_fetching,
            on_result=self._on_lyrics_update,
            config=self._config,
        )

    async def run(self) -> bool:
        """Run watch workflow and return success flag."""
        target_issue = self._target.validation_error()
        if target_issue:
            logger.error(target_issue)
            return False

        logger.info(
            "watch session starting (player filter: {})",
            self._player_hint or "<none>",
        )

        if not await self._control.start(self):
            return False
        try:
            await self._player_monitor.start()
            await self._tracker.start()
            self._calibration_task = asyncio.create_task(self._calibration_loop())
            self._schedule_emit()
            await asyncio.Event().wait()
            return True
        except asyncio.CancelledError:
            return True
        except Exception as exc:
            logger.exception("watch runtime error: {}", exc)
            return False
        finally:
            logger.info("watch session stopping")
            if self._calibration_task is not None:
                self._calibration_task.cancel()
                await asyncio.gather(self._calibration_task, return_exceptions=True)
                self._calibration_task = None
            await self._fetcher.stop()
            await self._tracker.stop()
            await self._player_monitor.close()
            await self._control.stop()

    async def _calibration_loop(self) -> None:
        """Periodically refresh full MPRIS snapshot as fallback calibration."""
        interval = max(0.1, self._config.watch.calibration_interval_s)
        while True:
            await asyncio.sleep(interval)
            try:
                await self._player_monitor.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("mpris calibration refresh failed: {}", exc)

    def _active_track(self) -> TrackMeta | None:
        """Return active track metadata from selected player."""
        player = self._player_monitor.players.get(self._model.active_player or "")
        return player.track if player else None

    def _request_fetch_for_active_track(self, reason: str) -> bool:
        """Trigger lyric fetch for active track when needed."""
        track = self._active_track()
        if track is None:
            return False
        if self._model.lyrics is not None:
            return False
        if self._model.status == "fetching":
            return False
        logger.info("fetching lyrics for track ({}): {}", reason, track.display_name())
        self._fetcher.request(track)
        return True

    async def _fetch_lyrics(self, track: TrackMeta) -> Optional[LRCData]:
        """Fetch lyrics in worker thread."""
        result = await asyncio.to_thread(
            self._manager.fetch_for_track,
            track,
            None,
            False,
            False,
        )
        if result and result.lyrics:
            return result.lyrics
        return None

    def _on_player_change(self) -> None:
        """React to monitor player snapshot change."""
        prev_player = self._model.active_player
        prev_track_key = self._model.active_track_key

        selected = ActivePlayerSelector.select(
            self._player_monitor.players,
            self._model.active_player,
            self._config,
        )
        self._model.active_player = selected

        if selected != prev_player:
            logger.info(
                "active player changed: {} -> {}",
                prev_player or "<none>",
                selected or "<none>",
            )

        if selected is None:
            self._model.status = "idle"
            self._model.active_track_key = None
            self._model.set_lyrics(None)
            self._schedule_emit()
            return

        state = self._player_monitor.players.get(selected)
        if state is None:
            self._model.status = "idle"
            self._model.active_track_key = None
            self._model.set_lyrics(None)
            self._schedule_emit()
            return

        track = state.track
        track_key = (
            track.trackid
            if track and track.trackid
            else track.display_name()
            if track
            else None
        )

        track_changed = track_key != prev_track_key
        player_changed = selected != prev_player
        if track_changed or player_changed:
            self._model.set_lyrics(None)

        self._model.active_track_key = track_key

        asyncio.create_task(
            self._tracker.set_active_player(
                selected,
                state.status,
                track_key,
            )
        )

        if state.status != "Playing":
            self._model.status = "paused"
            self._schedule_emit()
            return

        started_fetch = False
        if track is not None and (
            player_changed or track_changed or self._model.lyrics is None
        ):
            started_fetch = self._request_fetch_for_active_track("track-changed")

        if self._model.lyrics is not None:
            self._model.status = "ok"
        elif started_fetch:
            self._model.status = "fetching"
        elif self._model.status != "fetching":
            self._model.status = "no_lyrics"
        self._schedule_emit()

    def _on_seeked(self, bus_name: str, position_ms: int) -> None:
        """Forward seek event to tracker."""
        asyncio.create_task(self._tracker.on_seeked(bus_name, position_ms))

    def _on_playback_status(self, bus_name: str, status: str) -> None:
        """React to playback status change and tracker sync."""
        if bus_name == self._model.active_player:
            if status == "Playing":
                started_fetch = self._request_fetch_for_active_track("resume-playing")
                if self._model.lyrics is not None:
                    self._model.status = "ok"
                elif started_fetch:
                    self._model.status = "fetching"
                elif self._model.status != "fetching":
                    self._model.status = "no_lyrics"
            else:
                self._model.status = "paused"
            self._schedule_emit()
        asyncio.create_task(self._tracker.on_playback_status(bus_name, status))

    def _on_tracker_tick(self) -> None:
        """Emit updates from tracker tick only while lyrics are actively rendering."""
        if self._model.status == "ok":
            self._schedule_emit()

    def _schedule_emit(self) -> None:
        """Coalesce frequent events into at most one in-flight emit task."""
        if self._emit_scheduled:
            return
        self._emit_scheduled = True
        asyncio.create_task(self._run_scheduled_emit())

    async def _run_scheduled_emit(self) -> None:
        """Run one coalesced emit and release scheduler gate."""
        try:
            await self._emit_state()
        finally:
            self._emit_scheduled = False

    async def _on_fetching(self) -> None:
        """Mark model as fetching and emit state."""
        self._model.status = "fetching"
        await self._emit_state()

    async def _on_lyrics_update(self, lyrics: Optional[LRCData]) -> None:
        """Update model with fetched lyrics and emit state."""
        self._model.set_lyrics(lyrics)
        self._model.status = "ok" if lyrics is not None else "no_lyrics"
        logger.info(
            "lyrics update result: {}",
            "found" if lyrics is not None else "not found",
        )
        await self._emit_state()

    async def _emit_state(self) -> None:
        """Emit output state only when semantic signature changes."""
        player = self._player_monitor.players.get(self._model.active_player or "")
        track = player.track if player else None
        position = await self._tracker.get_position_ms()

        signature = self._view_model.signature(track, position)
        if signature == self._last_emit_signature:
            return
        self._last_emit_signature = signature
        state = self._view_model.state(track, position)
        await self._output.on_state(state)

    def handle_offset(self, delta: int) -> dict:
        """Apply offset update requested by control channel."""
        self._model.offset_ms += delta
        return {"ok": True, "offset_ms": self._model.offset_ms}

    def handle_status(self) -> dict:
        """Return status payload for control channel."""
        player = self._player_monitor.players.get(self._model.active_player or "")
        track = asdict(player.track) if player and player.track else None
        return {
            "ok": True,
            "offset_ms": self._model.offset_ms,
            "player": self._model.active_player,
            "track": track,
            "position_ms": self._tracker.peek_position_ms(),
            "lyrics_status": self._model.status,
        }
