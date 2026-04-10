from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from lrx_cli.lrc import LRCData
from lrx_cli.models import TrackMeta
from lrx_cli.watch.control import ControlClient, ControlServer, parse_delta
from lrx_cli.watch.view import BaseOutput, LyricView, WatchState, WatchStatus
from lrx_cli.watch.view.pipe import PipeOutput
from lrx_cli.watch.view.print import PrintOutput
from lrx_cli.watch.player import ActivePlayerSelector, PlayerState, PlayerTarget
from lrx_cli.config import AppConfig
from lrx_cli.watch.tracker import PositionTracker
from lrx_cli.watch.session import WatchCoordinator


TEST_CONFIG = AppConfig()
BUS = "org.mpris.MediaPlayer2.spotify"


def test_parse_delta_supports_plus_minus_and_reset() -> None:
    assert parse_delta("+200") == (True, 200, None)
    assert parse_delta("-150") == (True, -150, None)
    assert parse_delta("0") == (True, 0, None)


# PlayerTarget


def test_player_target_allows_all_when_hint_empty() -> None:
    target = PlayerTarget()
    assert target.allows("org.mpris.MediaPlayer2.spotify") is True
    assert target.allows("org.mpris.MediaPlayer2.mpd") is True


def test_player_target_filters_by_case_insensitive_substring() -> None:
    target = PlayerTarget("Spot")
    assert target.allows("org.mpris.MediaPlayer2.spotify") is True
    assert target.allows("org.mpris.MediaPlayer2.mpd") is False


def test_player_target_hint_allows_regardless_of_blacklist() -> None:
    # --player bypasses PLAYER_BLACKLIST; PlayerTarget.allows() reflects the hint only
    target = PlayerTarget("spot")
    assert target.allows("org.mpris.MediaPlayer2.spotify") is True


# ActivePlayerSelector


def _ps(bus: str, status: str = "Playing") -> PlayerState:
    return PlayerState(bus_name=bus, status=status, track=TrackMeta(title="T"))


def test_active_player_selector_returns_none_when_no_players() -> None:
    assert ActivePlayerSelector.select({}, None, "spotify") is None


def test_active_player_selector_prefers_single_playing() -> None:
    players = {
        "org.mpris.MediaPlayer2.foo": _ps("org.mpris.MediaPlayer2.foo", "Paused"),
        "org.mpris.MediaPlayer2.bar": _ps("org.mpris.MediaPlayer2.bar", "Playing"),
    }
    assert (
        ActivePlayerSelector.select(players, None, "spotify")
        == "org.mpris.MediaPlayer2.bar"
    )


def test_active_player_selector_prefers_keyword_among_multiple_playing() -> None:
    players = {
        "org.mpris.MediaPlayer2.foo": _ps("org.mpris.MediaPlayer2.foo"),
        "org.mpris.MediaPlayer2.spotify": _ps("org.mpris.MediaPlayer2.spotify"),
    }
    assert (
        ActivePlayerSelector.select(players, None, "spotify")
        == "org.mpris.MediaPlayer2.spotify"
    )


def test_active_player_selector_uses_last_active_when_no_playing() -> None:
    players = {
        "org.mpris.MediaPlayer2.foo": _ps("org.mpris.MediaPlayer2.foo", "Paused"),
        "org.mpris.MediaPlayer2.bar": _ps("org.mpris.MediaPlayer2.bar", "Stopped"),
    }
    assert (
        ActivePlayerSelector.select(players, "org.mpris.MediaPlayer2.bar", "spotify")
        == "org.mpris.MediaPlayer2.bar"
    )


def test_active_player_selector_falls_back_to_first_when_no_preference() -> None:
    players = {
        "org.mpris.MediaPlayer2.foo": _ps("org.mpris.MediaPlayer2.foo", "Paused"),
    }
    result = ActivePlayerSelector.select(players, None, "")
    assert result == "org.mpris.MediaPlayer2.foo"


# PositionTracker


def test_position_tracker_seeked_calibrates_immediately() -> None:
    async def _run() -> None:
        tracker = PositionTracker(lambda _: asyncio.sleep(0, result=1200), TEST_CONFIG)
        await tracker.start()
        await tracker.set_active_player(BUS, "Playing", "track-A")
        await tracker.on_seeked(BUS, 3500)
        pos = await tracker.get_position_ms()
        await tracker.stop()
        assert pos >= 3500

    asyncio.run(_run())


def test_position_tracker_pause_stops_position_growth() -> None:
    async def _run() -> None:
        tracker = PositionTracker(lambda _: asyncio.sleep(0, result=0), TEST_CONFIG)
        await tracker.start()
        await tracker.set_active_player(BUS, "Playing", "track-A")
        await asyncio.sleep(0.08)
        before = await tracker.get_position_ms()
        await tracker.on_playback_status(BUS, "Paused")
        await asyncio.sleep(0.08)
        after = await tracker.get_position_ms()
        await tracker.stop()
        assert before > 0
        assert after - before < 20

    asyncio.run(_run())


def test_position_tracker_resume_via_playback_status_calibrates() -> None:
    async def _run() -> None:
        tracker = PositionTracker(lambda _: asyncio.sleep(0, result=50000), TEST_CONFIG)
        await tracker.start()
        await tracker.set_active_player(BUS, "Paused", "track-A")
        await tracker.on_playback_status(BUS, "Playing")
        pos = await tracker.get_position_ms()
        await tracker.stop()
        assert pos >= 50000

    asyncio.run(_run())


def test_position_tracker_paused_start_calibrates_initial_position() -> None:
    """set_active_player with Paused must still calibrate position — player may be mid-song."""

    async def _run() -> None:
        tracker = PositionTracker(lambda _: asyncio.sleep(0, result=45000), TEST_CONFIG)
        await tracker.start()
        await tracker.set_active_player(BUS, "Paused", "track-A")
        pos = await tracker.get_position_ms()
        await tracker.stop()
        assert pos >= 45000

    asyncio.run(_run())


def test_position_tracker_resume_via_set_active_player_calibrates() -> None:
    async def _run() -> None:
        tracker = PositionTracker(lambda _: asyncio.sleep(0, result=42000), TEST_CONFIG)
        await tracker.start()
        await tracker.set_active_player(BUS, "Paused", "track-A")
        await tracker.set_active_player(BUS, "Playing", "track-A")
        pos = await tracker.get_position_ms()
        await tracker.stop()
        assert pos >= 42000

    asyncio.run(_run())


# ControlServer and ControlClient


def test_control_server_and_client_roundtrip(tmp_path: Path) -> None:
    async def _run() -> None:
        class _Session:
            def __init__(self) -> None:
                self.offset = 0

            def handle_offset(self, delta: int) -> dict:
                self.offset += delta
                return {"ok": True, "offset_ms": self.offset}

            def handle_status(self) -> dict:
                return {"ok": True, "offset_ms": self.offset, "lyrics_status": "idle"}

        socket_path = tmp_path / "watch.sock"
        server = ControlServer(socket_path=str(socket_path))
        await server.start(_Session())  # type: ignore
        client = ControlClient(socket_path=str(socket_path))
        r1 = await client._send_async({"cmd": "offset", "delta": 200})
        r2 = await client._send_async({"cmd": "status"})
        await server.stop()
        assert r1 == {"ok": True, "offset_ms": 200}
        assert r2["ok"] is True
        assert r2["offset_ms"] == 200

    asyncio.run(_run())


# PipeOutput


def _pipe_state(
    status: WatchStatus,
    lyrics: Optional[LRCData] = None,
    position_ms: int = 0,
    offset_ms: int = 0,
    track: Optional[TrackMeta] = None,
) -> WatchState:
    return WatchState(
        track=track,
        lyrics=LyricView.from_lrc(lyrics) if lyrics else None,
        position_ms=position_ms,
        offset_ms=offset_ms,
        status=status,
    )


def test_pipe_output_fetching_renders_status_window(capsys) -> None:
    asyncio.run(
        PipeOutput(before=1, after=1).on_state(_pipe_state(WatchStatus.FETCHING))
    )
    assert capsys.readouterr().out == "\n[fetching...]\n\n"


def test_pipe_output_no_lyrics_renders_status_window(capsys) -> None:
    asyncio.run(
        PipeOutput(before=1, after=1).on_state(_pipe_state(WatchStatus.NO_LYRICS))
    )
    assert capsys.readouterr().out == "\n[no lyrics]\n\n"


def test_pipe_output_idle_renders_status_window(capsys) -> None:
    asyncio.run(PipeOutput(before=1, after=1).on_state(_pipe_state(WatchStatus.IDLE)))
    assert capsys.readouterr().out == "\n[idle]\n\n"


def test_pipe_output_no_newline_mode(capsys) -> None:
    asyncio.run(
        PipeOutput(before=0, after=0, no_newline=True).on_state(
            _pipe_state(WatchStatus.FETCHING)
        )
    )
    assert capsys.readouterr().out == "[fetching...]"


def test_pipe_output_default_window_shows_current_line(capsys) -> None:
    lrc = LRCData("[00:01.00]a\n[00:02.00]b\n[00:03.00]c")
    asyncio.run(
        PipeOutput().on_state(_pipe_state(WatchStatus.OK, lrc, position_ms=2100))
    )
    assert capsys.readouterr().out == "b\n"


def test_pipe_output_context_window(capsys) -> None:
    lrc = LRCData("[00:01.00]a\n[00:02.00]b\n[00:03.00]c")
    asyncio.run(
        PipeOutput(before=1, after=1).on_state(
            _pipe_state(WatchStatus.OK, lrc, position_ms=2100)
        )
    )
    assert capsys.readouterr().out == "a\nb\nc\n"


def test_pipe_output_before_region_empty_at_first_line(capsys) -> None:
    lrc = LRCData("[00:01.00]a\n[00:02.00]b\n[00:03.00]c")
    asyncio.run(
        PipeOutput(before=1, after=1).on_state(
            _pipe_state(WatchStatus.OK, lrc, position_ms=1100)
        )
    )
    assert capsys.readouterr().out == "\na\nb\n"


def test_pipe_output_after_region_empty_at_last_line(capsys) -> None:
    lrc = LRCData("[00:01.00]a\n[00:02.00]b\n[00:03.00]c")
    asyncio.run(
        PipeOutput(before=1, after=1).on_state(
            _pipe_state(WatchStatus.OK, lrc, position_ms=3100)
        )
    )
    assert capsys.readouterr().out == "b\nc\n\n"


def test_pipe_output_upcoming_lines_before_first_timestamp(capsys) -> None:
    lrc = LRCData("[00:02.00]a\n[00:03.00]b")
    asyncio.run(
        PipeOutput(before=1, after=1).on_state(
            _pipe_state(WatchStatus.OK, lrc, position_ms=0)
        )
    )
    assert capsys.readouterr().out == "\n\na\n"


def test_pipe_output_offset_ms_shifts_effective_position(capsys) -> None:
    lrc = LRCData("[00:01.00]a\n[00:02.00]b\n[00:03.00]c")
    asyncio.run(
        PipeOutput().on_state(
            _pipe_state(WatchStatus.OK, lrc, position_ms=1000, offset_ms=1500)
        )
    )
    # effective = 2500 ms → line b
    assert capsys.readouterr().out == "b\n"


def test_pipe_output_repeated_text_uses_correct_timed_occurrence(capsys) -> None:
    lrc = LRCData("[00:01.00]A\n[00:02.00]X\n[00:03.00]B\n[00:04.00]X\n[00:05.00]C")
    asyncio.run(
        PipeOutput(before=1, after=1).on_state(
            _pipe_state(WatchStatus.OK, lrc, position_ms=4100)
        )
    )
    assert capsys.readouterr().out == "B\nX\nC\n"


# PrintOutput


def _ok_state(lyrics: LRCData, track: Optional[TrackMeta] = None) -> WatchState:
    return WatchState(
        track=track or TrackMeta(title="Song", artist="Artist"),
        lyrics=LyricView.from_lrc(lyrics),
        position_ms=0,
        offset_ms=0,
        status=WatchStatus.OK,
    )


def _status_state(status: WatchStatus, track: Optional[TrackMeta] = None) -> WatchState:
    return WatchState(
        track=track or TrackMeta(title="Song", artist="Artist"),
        lyrics=None,
        position_ms=0,
        offset_ms=0,
        status=status,
    )


def test_print_output_emits_lrc_on_ok(capsys) -> None:
    asyncio.run(
        PrintOutput().on_state(_ok_state(LRCData("[00:01.00]Hello\n[00:02.00]World")))
    )
    assert capsys.readouterr().out.startswith("[00:01.00]")


def test_print_output_plain_strips_tags(capsys) -> None:
    asyncio.run(
        PrintOutput(plain=True).on_state(
            _ok_state(LRCData("[00:01.00]Hello\n[00:02.00]World"))
        )
    )
    out = capsys.readouterr().out
    assert "[" not in out
    assert "Hello" in out


def test_print_output_plain_with_unsynced_lyrics(capsys) -> None:
    asyncio.run(PrintOutput(plain=True).on_state(_ok_state(LRCData("Hello\nWorld"))))
    out = capsys.readouterr().out
    assert "Hello" in out
    assert "[" not in out


def test_print_output_no_lyrics_emits_blank_line(capsys) -> None:
    asyncio.run(PrintOutput().on_state(_status_state(WatchStatus.NO_LYRICS)))
    assert capsys.readouterr().out == "\n"


def test_print_output_fetching_emits_nothing(capsys) -> None:
    asyncio.run(PrintOutput().on_state(_status_state(WatchStatus.FETCHING)))
    assert capsys.readouterr().out == ""


def test_print_output_idle_emits_nothing(capsys) -> None:
    asyncio.run(PrintOutput().on_state(_status_state(WatchStatus.IDLE)))
    assert capsys.readouterr().out == ""


def test_print_output_is_stateless(capsys) -> None:
    """View has no internal deduplication — emits on every call."""
    output = PrintOutput()
    state = _ok_state(LRCData("[00:01.00]Hello"))
    asyncio.run(output.on_state(state))
    asyncio.run(output.on_state(state))
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln]
    assert len(lines) == 2


def test_print_output_position_sensitive_is_false() -> None:
    assert PrintOutput.position_sensitive is False


# WatchCoordinator


class _CaptureFetcher:
    def __init__(self) -> None:
        self.requested: list[str] = []

    def request(self, track: TrackMeta) -> None:
        self.requested.append(track.display_name())

    async def stop(self) -> None:
        pass


def _make_coordinator(output: Optional[BaseOutput] = None) -> WatchCoordinator:
    class _Manager:
        def fetch_for_track(self, *_a, **_kw):
            return None

    class _NullOutput(BaseOutput):
        async def on_state(self, state: WatchState) -> None:
            pass

    session = WatchCoordinator(
        _Manager(),  # type: ignore
        output or _NullOutput(),
        player_hint=None,
        config=TEST_CONFIG,
    )
    session._tracker = PositionTracker(
        lambda _bus: asyncio.sleep(0, result=0),
        TEST_CONFIG,
    )
    return session


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
    """Fetch starts immediately even when player is paused — no wait for resume."""

    async def _run() -> None:
        session = _make_coordinator()
        fetcher = _CaptureFetcher()
        session._fetcher = fetcher  # type: ignore[assignment]
        session._player_monitor.players = {BUS: _pstate("Paused")}
        session._on_player_change()
        await asyncio.sleep(0)
        assert fetcher.requested == ["Artist - Song"]

    asyncio.run(_run())


def test_coordinator_paused_start_emits_correct_line_after_fetch() -> None:
    """After fetch completes with a mid-song paused player, the current lyric line must render."""

    async def _run() -> None:
        received: list[WatchState] = []

        class _CaptureOutput(BaseOutput):
            position_sensitive = True

            async def on_state(self, state: WatchState) -> None:
                received.append(state)

        class _Manager:
            def fetch_for_track(self, *_a, **_kw):
                return None

        PAUSED_MS = 45000
        lrc = LRCData("[00:43.00]a\n[00:44.00]b\n[00:46.00]c")

        session = WatchCoordinator(
            _Manager(),  # type: ignore
            _CaptureOutput(),
            player_hint=None,
            config=TEST_CONFIG,
        )
        session._tracker = PositionTracker(
            lambda _bus: asyncio.sleep(0, result=PAUSED_MS),
            TEST_CONFIG,
        )
        await session._tracker.start()

        # Calibrate tracker directly (tracker-level behavior already covered by
        # test_position_tracker_paused_start_calibrates_initial_position)
        await session._tracker.set_active_player(BUS, "Paused", "Artist - Song")

        # Put model in the state _on_player_change would have produced
        session._model.active_player = BUS
        session._model.active_track_key = "Artist - Song"
        session._model.status = WatchStatus.FETCHING
        session._player_monitor.players = {BUS: _pstate("Paused")}
        session._last_emit_signature = (
            "status",
            WatchStatus.FETCHING,
            BUS,
            "Artist - Song",
        )

        await session._on_lyrics_update(lrc)

        last_ok = next(
            (s for s in reversed(received) if s.status == WatchStatus.OK), None
        )
        assert last_ok is not None, "no OK state emitted after lyrics loaded"
        assert last_ok.position_ms >= PAUSED_MS

        await session._tracker.stop()

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
        assert session._model.lyrics is None

    asyncio.run(_run())


def test_coordinator_no_refetch_on_calibration_no_lyrics() -> None:
    """Calibration with same player/track and no_lyrics must NOT trigger a second fetch."""

    async def _run() -> None:
        session = _make_coordinator()
        fetcher = _CaptureFetcher()
        session._fetcher = fetcher  # type: ignore[assignment]
        session._player_monitor.players = {BUS: _pstate("Playing")}
        session._on_player_change()
        await asyncio.sleep(0)
        assert len(fetcher.requested) == 1

        session._model.status = WatchStatus.NO_LYRICS
        session._on_player_change()
        await asyncio.sleep(0)
        assert len(fetcher.requested) == 1

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


def test_coordinator_player_disappears_goes_idle() -> None:
    async def _run() -> None:
        session = _make_coordinator()
        session._model.active_player = BUS
        session._model.active_track_key = "Artist - Song"
        session._model.set_lyrics(LRCData("[00:01.00]line"))
        session._model.status = WatchStatus.OK

        session._player_monitor.players = {}
        session._on_player_change()
        await asyncio.sleep(0)

        assert session._model.status == WatchStatus.IDLE
        assert session._model.lyrics is None
        assert session._model.active_player is None

    asyncio.run(_run())


def test_coordinator_no_fetch_when_track_is_none() -> None:
    """Player present but reports no track metadata → no fetch, status NO_LYRICS."""

    async def _run() -> None:
        session = _make_coordinator()
        fetcher = _CaptureFetcher()
        session._fetcher = fetcher  # type: ignore[assignment]
        session._player_monitor.players = {
            BUS: PlayerState(bus_name=BUS, status="Playing", track=None)
        }
        session._on_player_change()
        await asyncio.sleep(0)

        assert fetcher.requested == []
        assert session._model.status == WatchStatus.NO_LYRICS

    asyncio.run(_run())


def test_coordinator_emit_deduplicates_on_same_cursor() -> None:
    async def _run() -> None:
        counts = [0]

        class _CountOutput(BaseOutput):
            async def on_state(self, state: WatchState) -> None:
                counts[0] += 1

        session = _make_coordinator(_CountOutput())
        track = TrackMeta(title="Song", artist="Artist")
        session._model.active_player = BUS
        session._player_monitor.players = {
            BUS: PlayerState(bus_name=BUS, status="Playing", track=track)
        }
        session._model.set_lyrics(LRCData("[00:01.00]a\n[00:03.00]b"))
        session._model.status = WatchStatus.OK
        await session._tracker.set_active_player(BUS, "Playing", "Artist - Song")

        await session._emit_state()  # emits
        await session._emit_state()  # same cursor → no emit
        assert counts[0] == 1

        await session._tracker.on_seeked(BUS, 3200)
        await session._emit_state()  # cursor advanced → emits
        assert counts[0] == 2

    asyncio.run(_run())


def test_coordinator_position_insensitive_output_ignores_seeks() -> None:
    """With position_sensitive=False, seek events do not trigger re-emit."""

    async def _run() -> None:
        counts = [0]

        class _CountPrint(PrintOutput):
            async def on_state(self, state: WatchState) -> None:
                counts[0] += 1

        session = _make_coordinator(_CountPrint())
        track = TrackMeta(title="Song", artist="Artist")
        session._model.active_player = BUS
        session._player_monitor.players = {
            BUS: PlayerState(bus_name=BUS, status="Playing", track=track)
        }
        session._model.set_lyrics(LRCData("[00:01.00]a\n[00:03.00]b"))
        session._model.status = WatchStatus.OK

        await session._emit_state()  # emits once
        assert counts[0] == 1

        await session._tracker.on_seeked(BUS, 3200)
        await session._emit_state()  # position fixed at 0 → same signature → no re-emit
        assert counts[0] == 1

    asyncio.run(_run())
