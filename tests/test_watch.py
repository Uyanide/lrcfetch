from __future__ import annotations

import asyncio
from pathlib import Path

from lrx_cli.lrc import LRCData
from lrx_cli.models import TrackMeta
from lrx_cli.watch.control import ControlClient, ControlServer, parse_delta
from lrx_cli.watch.view import BaseOutput, LyricView, WatchState, WatchStatus
from lrx_cli.watch.view.pipe import PipeOutput
from lrx_cli.watch.player import ActivePlayerSelector, PlayerState, PlayerTarget
from lrx_cli.watch.fetcher import LyricFetcher
from lrx_cli.config import AppConfig
from lrx_cli.watch.tracker import PositionTracker
from lrx_cli.watch.session import WatchCoordinator


TEST_CONFIG = AppConfig()


def test_parse_delta_supports_plus_minus_and_reset() -> None:
    assert parse_delta("+200") == (True, 200, None)
    assert parse_delta("-150") == (True, -150, None)
    assert parse_delta("0") == (True, 0, None)


def test_player_target_allows_all_when_hint_empty() -> None:
    target = PlayerTarget()

    assert target.allows("org.mpris.MediaPlayer2.spotify") is True
    assert target.allows("org.mpris.MediaPlayer2.mpd") is True


def test_player_target_filters_by_case_insensitive_substring() -> None:
    target = PlayerTarget("Spot")

    assert target.allows("org.mpris.MediaPlayer2.spotify") is True
    assert target.allows("org.mpris.MediaPlayer2.mpd") is False


def test_player_target_reports_blacklisted_hint() -> None:
    target = PlayerTarget("spot", player_blacklist=("spotify",))
    assert target.validation_error() is not None


def test_active_player_selector_prefers_single_playing() -> None:
    players = {
        "org.mpris.MediaPlayer2.foo": PlayerState(
            bus_name="org.mpris.MediaPlayer2.foo",
            status="Paused",
            track=TrackMeta(title="A"),
        ),
        "org.mpris.MediaPlayer2.bar": PlayerState(
            bus_name="org.mpris.MediaPlayer2.bar",
            status="Playing",
            track=TrackMeta(title="B"),
        ),
    }
    assert (
        ActivePlayerSelector.select(players, None, TEST_CONFIG.general.preferred_player)
        == "org.mpris.MediaPlayer2.bar"
    )


def test_active_player_selector_uses_last_active_when_no_playing() -> None:
    players = {
        "org.mpris.MediaPlayer2.foo": PlayerState(
            bus_name="org.mpris.MediaPlayer2.foo",
            status="Paused",
            track=TrackMeta(title="A"),
        ),
        "org.mpris.MediaPlayer2.bar": PlayerState(
            bus_name="org.mpris.MediaPlayer2.bar",
            status="Stopped",
            track=TrackMeta(title="B"),
        ),
    }

    assert (
        ActivePlayerSelector.select(
            players,
            "org.mpris.MediaPlayer2.bar",
            TEST_CONFIG.general.preferred_player,
        )
        == "org.mpris.MediaPlayer2.bar"
    )


def test_position_tracker_seeked_calibrates_immediately() -> None:
    async def _run() -> None:
        async def _poll(_bus: str):
            return 1200

        tracker = PositionTracker(_poll, TEST_CONFIG)
        await tracker.start()
        await tracker.set_active_player(
            "org.mpris.MediaPlayer2.foo", "Playing", "track-A"
        )
        await tracker.on_seeked("org.mpris.MediaPlayer2.foo", 3500)
        pos = await tracker.get_position_ms()
        await tracker.stop()
        assert pos >= 3500

    asyncio.run(_run())


def test_position_tracker_playback_status_pause_stops_fast_growth() -> None:
    async def _run() -> None:
        async def _poll(_bus: str):
            return 0

        tracker = PositionTracker(_poll, TEST_CONFIG)
        await tracker.start()
        await tracker.set_active_player(
            "org.mpris.MediaPlayer2.foo", "Playing", "track-A"
        )
        await asyncio.sleep(0.08)
        before = await tracker.get_position_ms()

        await tracker.on_playback_status("org.mpris.MediaPlayer2.foo", "Paused")
        await asyncio.sleep(0.08)
        after = await tracker.get_position_ms()
        await tracker.stop()

        assert before > 0
        assert after - before < 20

    asyncio.run(_run())


def test_position_tracker_playback_status_playing_calibrates_once() -> None:
    async def _run() -> None:
        async def _poll(_bus: str):
            return 50000

        tracker = PositionTracker(_poll, TEST_CONFIG)
        await tracker.start()
        await tracker.set_active_player(
            "org.mpris.MediaPlayer2.foo", "Paused", "track-A"
        )
        await tracker.on_playback_status("org.mpris.MediaPlayer2.foo", "Playing")
        pos = await tracker.get_position_ms()
        await tracker.stop()

        assert pos >= 50000

    asyncio.run(_run())


def test_position_tracker_set_active_player_playing_calibrates_on_resume() -> None:
    async def _run() -> None:
        async def _poll(_bus: str):
            return 42000

        tracker = PositionTracker(_poll, TEST_CONFIG)
        await tracker.start()
        await tracker.set_active_player(
            "org.mpris.MediaPlayer2.foo", "Paused", "track-A"
        )
        await tracker.set_active_player(
            "org.mpris.MediaPlayer2.foo", "Playing", "track-A"
        )
        pos = await tracker.get_position_ms()
        await tracker.stop()

        assert pos >= 42000

    asyncio.run(_run())


def test_control_server_and_client_roundtrip(tmp_path: Path) -> None:
    async def _run() -> None:
        class _Session:
            def __init__(self):
                self.offset = 0

            def handle_offset(self, delta: int) -> dict:
                self.offset += delta
                return {"ok": True, "offset_ms": self.offset}

            def handle_status(self) -> dict:
                return {"ok": True, "offset_ms": self.offset, "lyrics_status": "idle"}

        socket_path = tmp_path / "watch.sock"
        server = ControlServer(socket_path=str(socket_path))
        session = _Session()

        await server.start(session)  # type: ignore
        client = ControlClient(socket_path=str(socket_path))
        r1 = await client._send_async({"cmd": "offset", "delta": 200})
        r2 = await client._send_async({"cmd": "status"})
        await server.stop()

        assert r1 == {"ok": True, "offset_ms": 200}
        assert r2["ok"] is True
        assert r2["offset_ms"] == 200

    asyncio.run(_run())


def test_pipe_output_prints_fixed_window_for_status(capsys) -> None:
    output = PipeOutput(before=1, after=1)
    state = WatchState(
        track=None,
        lyrics=None,
        position_ms=0,
        offset_ms=0,
        status="fetching",
    )

    asyncio.run(output.on_state(state))

    printed = capsys.readouterr().out
    assert printed == "\n[fetching...]\n\n"


def test_pipe_output_uses_context_window_for_lyrics(capsys) -> None:
    output = PipeOutput(before=1, after=1)
    lyrics = LRCData("[00:01.00]a\n[00:02.00]b\n[00:03.00]c")
    state = WatchState(
        track=TrackMeta(title="Song"),
        lyrics=LyricView.from_lrc(lyrics),
        position_ms=2100,
        offset_ms=0,
        status="ok",
    )

    asyncio.run(output.on_state(state))

    printed = capsys.readouterr().out
    assert printed == "a\nb\nc\n"


def test_pipe_output_shows_upcoming_lines_before_first_timestamp(capsys) -> None:
    output = PipeOutput(before=1, after=1)
    lyrics = LRCData("[00:02.00]a\n[00:03.00]b")
    state = WatchState(
        track=TrackMeta(title="Song"),
        lyrics=LyricView.from_lrc(lyrics),
        position_ms=0,
        offset_ms=0,
        status="ok",
    )

    asyncio.run(output.on_state(state))

    printed = capsys.readouterr().out
    assert printed == "\n\na\n"


def test_pipe_output_first_line_keeps_before_region_empty(capsys) -> None:
    output = PipeOutput(before=1, after=1)
    lyrics = LRCData("[00:01.00]a\n[00:02.00]b\n[00:03.00]c")
    state = WatchState(
        track=TrackMeta(title="Song"),
        lyrics=LyricView.from_lrc(lyrics),
        position_ms=1100,
        offset_ms=0,
        status="ok",
    )

    asyncio.run(output.on_state(state))

    printed = capsys.readouterr().out
    assert printed == "\na\nb\n"


def test_pipe_output_last_line_keeps_after_region_empty(capsys) -> None:
    output = PipeOutput(before=1, after=1)
    lyrics = LRCData("[00:01.00]a\n[00:02.00]b\n[00:03.00]c")
    state = WatchState(
        track=TrackMeta(title="Song"),
        lyrics=LyricView.from_lrc(lyrics),
        position_ms=3100,
        offset_ms=0,
        status="ok",
    )

    asyncio.run(output.on_state(state))

    printed = capsys.readouterr().out
    assert printed == "b\nc\n\n"


def test_pipe_output_repeated_text_uses_correct_timed_occurrence(capsys) -> None:
    output = PipeOutput(before=1, after=1)
    lyrics = LRCData("[00:01.00]A\n[00:02.00]X\n[00:03.00]B\n[00:04.00]X\n[00:05.00]C")
    state = WatchState(
        track=TrackMeta(title="Song"),
        lyrics=LyricView.from_lrc(lyrics),
        position_ms=4100,
        offset_ms=0,
        status="ok",
    )

    asyncio.run(output.on_state(state))

    printed = capsys.readouterr().out
    assert printed == "B\nX\nC\n"


# ── WatchCoordinator state machine ───────────────────────────────────────────


class _CaptureFetcher:
    """Records fetch requests without doing real network calls."""

    def __init__(self) -> None:
        self.requested: list[str] = []

    def request(self, track: TrackMeta) -> None:
        self.requested.append(track.display_name())

    async def stop(self) -> None:
        pass


def _make_coordinator() -> WatchCoordinator:
    class _Manager:
        def fetch_for_track(self, *_a, **_kw):
            return None

    class _Output(BaseOutput):
        async def on_state(self, state: WatchState) -> None:
            pass

    session = WatchCoordinator(
        _Manager(),  # type: ignore
        _Output(),
        player_hint=None,
        config=TEST_CONFIG,
    )
    session._tracker = PositionTracker(
        lambda _bus: asyncio.sleep(0, result=0),
        TEST_CONFIG,
    )
    return session


BUS = "org.mpris.MediaPlayer2.spotify"


def _pstate(status: str = "Playing", title: str = "Song") -> PlayerState:
    return PlayerState(
        bus_name=BUS,
        status=status,
        track=TrackMeta(title=title, artist="Artist"),
    )


def test_coordinator_fetches_on_initial_player() -> None:
    async def _run() -> None:
        session = _make_coordinator()
        fetcher = _CaptureFetcher()
        session._fetcher = fetcher  # type: ignore[assignment]

        session._player_monitor.players = {BUS: _pstate("Playing")}
        session._on_player_change()
        await asyncio.sleep(0)

        assert fetcher.requested == ["Artist - Song"]
        assert session._model.status == WatchStatus.FETCHING

    asyncio.run(_run())


def test_coordinator_fetches_while_paused() -> None:
    """Fetch is triggered immediately even when player is paused."""

    async def _run() -> None:
        session = _make_coordinator()
        fetcher = _CaptureFetcher()
        session._fetcher = fetcher  # type: ignore[assignment]

        session._player_monitor.players = {BUS: _pstate("Paused")}
        session._on_player_change()
        await asyncio.sleep(0)

        assert fetcher.requested == ["Artist - Song"]

    asyncio.run(_run())


def test_coordinator_fetches_on_track_change() -> None:
    async def _run() -> None:
        session = _make_coordinator()
        session._model.active_player = BUS
        session._model.active_track_key = "Artist - Old Song"
        session._model.set_lyrics(LRCData("[00:01.00]old"))
        session._model.status = WatchStatus.OK

        fetcher = _CaptureFetcher()
        session._fetcher = fetcher  # type: ignore[assignment]

        session._player_monitor.players = {BUS: _pstate("Playing", title="New Song")}
        session._on_player_change()
        await asyncio.sleep(0)

        assert fetcher.requested == ["Artist - New Song"]
        assert session._model.lyrics is None  # cleared on track change

    asyncio.run(_run())


def test_coordinator_no_refetch_on_calibration_no_lyrics() -> None:
    """Calibration with same player/track and no_lyrics must NOT trigger a second fetch."""

    async def _run() -> None:
        session = _make_coordinator()
        fetcher = _CaptureFetcher()
        session._fetcher = fetcher  # type: ignore[assignment]

        session._player_monitor.players = {BUS: _pstate("Playing")}
        session._on_player_change()  # first call: player appears → fetch
        await asyncio.sleep(0)
        assert len(fetcher.requested) == 1

        session._model.status = WatchStatus.NO_LYRICS  # simulate fetch returned nothing

        session._on_player_change()  # calibration: same player/track
        await asyncio.sleep(0)
        assert len(fetcher.requested) == 1  # no second fetch

    asyncio.run(_run())


def test_coordinator_no_fetch_when_lyrics_present() -> None:
    async def _run() -> None:
        session = _make_coordinator()
        session._model.active_player = BUS
        session._model.active_track_key = "Artist - Song"
        session._model.set_lyrics(LRCData("[00:01.00]line"))
        session._model.status = WatchStatus.OK

        fetcher = _CaptureFetcher()
        session._fetcher = fetcher  # type: ignore[assignment]

        session._player_monitor.players = {BUS: _pstate("Playing")}
        session._on_player_change()
        await asyncio.sleep(0)

        assert fetcher.requested == []
        assert session._model.status == WatchStatus.OK

    asyncio.run(_run())


def test_session_emit_state_only_when_lyric_cursor_changes() -> None:
    async def _run() -> None:
        class _Manager:
            def fetch_for_track(self, *_args, **_kwargs):
                return None

        class _Output(BaseOutput):
            def __init__(self):
                self.count = 0

            async def on_state(self, state: WatchState) -> None:
                self.count += 1

        output = _Output()
        session = WatchCoordinator(
            _Manager(),  # type: ignore
            output,
            player_hint=None,
            config=TEST_CONFIG,
        )
        session._tracker = PositionTracker(
            lambda _bus: asyncio.sleep(0, result=0),
            TEST_CONFIG,
        )

        bus_name = "org.mpris.MediaPlayer2.spotify"
        track = TrackMeta(title="Song", artist="Artist")
        session._model.active_player = bus_name
        session._player_monitor.players = {
            bus_name: PlayerState(bus_name=bus_name, status="Playing", track=track)
        }
        session._model.set_lyrics(LRCData("[00:01.00]a\n[00:03.00]b"))
        session._model.status = WatchStatus.OK
        await session._tracker.set_active_player(
            bus_name,
            "Playing",
            "Artist - Song",
        )

        await session._emit_state()
        await session._emit_state()

        await session._tracker.on_seeked(bus_name, 3200)
        await session._emit_state()

        assert output.count == 2

    asyncio.run(_run())


def test_session_emits_when_crossing_first_timestamp() -> None:
    async def _run() -> None:
        class _Manager:
            def fetch_for_track(self, *_args, **_kwargs):
                return None

        class _Output(BaseOutput):
            def __init__(self):
                self.count = 0

            async def on_state(self, state: WatchState) -> None:
                self.count += 1

        output = _Output()
        session = WatchCoordinator(
            _Manager(),  # type: ignore
            output,
            player_hint=None,
            config=TEST_CONFIG,
        )
        session._tracker = PositionTracker(
            lambda _bus: asyncio.sleep(0, result=0),
            TEST_CONFIG,
        )

        bus_name = "org.mpris.MediaPlayer2.spotify"
        track = TrackMeta(title="Song", artist="Artist")
        session._model.active_player = bus_name
        session._player_monitor.players = {
            bus_name: PlayerState(bus_name=bus_name, status="Playing", track=track)
        }
        session._model.set_lyrics(LRCData("[00:02.00]a\n[00:03.00]b"))
        session._model.status = WatchStatus.OK
        await session._tracker.set_active_player(
            bus_name,
            "Playing",
            "Artist - Song",
        )

        await session._emit_state()
        await session._tracker.on_seeked(bus_name, 2500)
        await session._emit_state()

        assert output.count == 2

    asyncio.run(_run())
