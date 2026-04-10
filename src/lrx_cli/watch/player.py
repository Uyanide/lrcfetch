"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-04-10 08:14:27
Description: Player discovery, state monitoring, and active-player selection for watch mode.
"""

from __future__ import annotations


from dataclasses import dataclass
from typing import Callable, Optional
import asyncio

from dbus_next.aio.message_bus import MessageBus
from dbus_next.constants import BusType
from dbus_next.message import Message
from loguru import logger

from ..models import TrackMeta
from ..mpris import pick_active_player


def _variant_value(item: object) -> object | None:
    """Extract .value from DBus variant-like objects when available."""
    if hasattr(item, "value"):
        return getattr(item, "value")
    return None


@dataclass(slots=True)
class PlayerState:
    """Current observable state for one MPRIS player."""

    bus_name: str
    status: str
    track: Optional[TrackMeta]


@dataclass(frozen=True, slots=True)
class PlayerTarget:
    """Constraint for choosing which players are visible to watch."""

    hint: Optional[str] = None

    @property
    def normalized_hint(self) -> str:
        """Return normalized lowercase player hint string."""
        return (self.hint or "").strip().lower()

    def allows(self, bus_name: str) -> bool:
        """Return whether given MPRIS bus name passes this target constraint."""
        normalized_hint = self.normalized_hint
        if not normalized_hint:
            return True
        return _keyword_match(bus_name, normalized_hint)


def _keyword_match(text: str, keyword: str) -> bool:
    """Return True when keyword exists in text, case-insensitively."""
    return keyword.strip().lower() in text.lower()


class PlayerMonitor:
    """Tracks MPRIS players and forwards signal-driven state updates to session callbacks."""

    _player_blacklist: tuple[str, ...]
    _on_players_changed: Callable[[], None]
    _on_seeked: Callable[[str, int], None]
    _on_playback_status: Callable[[str, str], None]
    _target: PlayerTarget
    players: dict[str, PlayerState]
    _bus: MessageBus | None
    _props_cache: dict[str, object]

    def __init__(
        self,
        on_players_changed: Callable[[], None],
        on_seeked: Callable[[str, int], None],
        on_playback_status: Callable[[str, str], None],
        player_blacklist: tuple[str, ...],
        target: Optional[PlayerTarget] = None,
    ) -> None:
        """Initialize monitor callbacks, runtime options, and player target filter."""
        self._player_blacklist = player_blacklist
        self._on_players_changed = on_players_changed
        self._on_seeked = on_seeked
        self._on_playback_status = on_playback_status
        self._target = target or PlayerTarget()
        self.players: dict[str, PlayerState] = {}
        self._bus: MessageBus | None = None
        self._props_cache: dict[str, object] = {}

    async def start(self) -> None:
        """Start DBus monitoring and populate initial player snapshot."""
        self._bus = await MessageBus(bus_type=BusType.SESSION).connect()
        self._bus.add_message_handler(self._on_message)
        await self._add_match_rules()
        await self.refresh()

    async def close(self) -> None:
        """Stop DBus monitoring and close bus connection."""
        self._props_cache.clear()
        if self._bus:
            self._bus.disconnect()
            self._bus = None

    async def _get_player_props(self, bus_name: str) -> object | None:
        """Return cached DBus Properties interface for player, creating it if missing."""
        if not self._bus:
            return None
        if bus_name in self._props_cache:
            return self._props_cache[bus_name]

        try:
            introspection = await self._bus.introspect(
                bus_name, "/org/mpris/MediaPlayer2"
            )
            proxy = self._bus.get_proxy_object(
                bus_name, "/org/mpris/MediaPlayer2", introspection
            )
            props = proxy.get_interface("org.freedesktop.DBus.Properties")
            self._props_cache[bus_name] = props
            return props
        except Exception as e:
            logger.debug(f"Failed to prepare DBus props for {bus_name}: {e}")
            self._props_cache.pop(bus_name, None)
            return None

    async def _add_match_rules(self) -> None:
        """Register signal subscriptions needed by monitor."""
        if not self._bus:
            return
        rules = [
            "type='signal',interface='org.freedesktop.DBus',member='NameOwnerChanged'",
            "type='signal',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged'",
            "type='signal',interface='org.mpris.MediaPlayer2.Player',member='Seeked'",
        ]
        for rule in rules:
            try:
                await self._bus.call(
                    Message(
                        destination="org.freedesktop.DBus",
                        path="/org/freedesktop/DBus",
                        interface="org.freedesktop.DBus",
                        member="AddMatch",
                        signature="s",
                        body=[rule],
                    )
                )
            except Exception as e:
                logger.debug(f"Failed to add DBus match rule {rule}: {e}")

    async def _list_mpris_players(self) -> list[str]:
        """List visible MPRIS players after applying target filter and optional blacklist.

        The blacklist is skipped when an explicit player hint is active so that
        ``--player`` can target any player regardless of PLAYER_BLACKLIST.
        """
        if not self._bus:
            return []
        try:
            reply = await self._bus.call(
                Message(
                    destination="org.freedesktop.DBus",
                    path="/org/freedesktop/DBus",
                    interface="org.freedesktop.DBus",
                    member="ListNames",
                )
            )
            if not reply or not reply.body:
                return []
            out: list[str] = []
            hint_active = bool(self._target.normalized_hint)
            for name in reply.body[0]:
                if not name.startswith("org.mpris.MediaPlayer2."):
                    continue
                # --player bypasses the blacklist; only filter when no hint is given
                if not hint_active and any(
                    x.lower() in name.lower() for x in self._player_blacklist
                ):
                    continue
                if not self._target.allows(name):
                    continue
                out.append(name)
            return out
        except Exception as e:
            logger.debug(f"Failed to list mpris players: {e}")
            return []

    async def _fetch_player_state(self, bus_name: str) -> Optional[PlayerState]:
        """Read current playback status and metadata from one player service."""
        props = await self._get_player_props(bus_name)
        if props is None:
            return None
        try:
            status_var = await getattr(props, "call_get")(
                "org.mpris.MediaPlayer2.Player", "PlaybackStatus"
            )
            metadata_var = await getattr(props, "call_get")(
                "org.mpris.MediaPlayer2.Player", "Metadata"
            )
            status = status_var.value if status_var else "Stopped"
            track = self._track_from_metadata(
                metadata_var.value if metadata_var else {}
            )
            return PlayerState(bus_name=bus_name, status=status, track=track)
        except Exception as e:
            logger.debug(f"Failed to read state for {bus_name}: {e}")
            self._props_cache.pop(bus_name, None)
            return None

    def _track_from_metadata(self, metadata: dict[str, object]) -> Optional[TrackMeta]:
        """Build TrackMeta object from MPRIS metadata map."""
        if not metadata:
            return None
        trackid = metadata.get("mpris:trackid")
        if trackid is not None:
            trackid = _variant_value(trackid)
            # normalize Spotify track IDs — the raw MPRIS value varies by client version
            if isinstance(trackid, str) and trackid.startswith("spotify:track:"):
                trackid = trackid.removeprefix("spotify:track:")
            elif isinstance(trackid, str) and trackid.startswith("/com/spotify/track/"):
                trackid = trackid.removeprefix("/com/spotify/track/")
            elif not isinstance(trackid, str):
                trackid = None

        length = metadata.get("mpris:length")
        length_ms = None
        length_value = _variant_value(length) if length is not None else None
        if isinstance(length_value, int):
            # MPRIS reports length in microseconds; convert to milliseconds
            length_ms = length_value // 1000

        artist = metadata.get("xesam:artist")
        artist_v = None
        artist_value = _variant_value(artist) if artist is not None else None
        if isinstance(artist_value, list) and artist_value:
            # xesam:artist is a list; take the first entry as primary artist
            artist_v = artist_value[0]

        title = metadata.get("xesam:title")
        album = metadata.get("xesam:album")
        url = metadata.get("xesam:url")

        title_value = _variant_value(title) if title is not None else None
        album_value = _variant_value(album) if album is not None else None
        url_value = _variant_value(url) if url is not None else None

        return TrackMeta(
            trackid=trackid,
            length=length_ms,
            album=album_value if isinstance(album_value, str) else None,
            artist=artist_v,
            title=title_value if isinstance(title_value, str) else None,
            url=url_value if isinstance(url_value, str) else None,
        )

    async def refresh(self) -> None:
        """Refresh full player snapshot and notify session when visible set changes."""
        players = await self._list_mpris_players()
        updated: dict[str, PlayerState] = {}
        for bus_name in players:
            st = await self._fetch_player_state(bus_name)
            if st is not None:
                updated[bus_name] = st

        before = set(self.players.keys())
        after = set(updated.keys())
        added = sorted(after - before)
        removed = sorted(before - after)

        for bus_name in removed:
            self._props_cache.pop(bus_name, None)

        self.players = updated

        if added or removed:
            logger.info(
                "MPRIS players updated: added={}, removed={}",
                added,
                removed,
            )

        self._on_players_changed()

    async def _resolve_well_known_name(self, unique_sender: str) -> str | None:
        """Map a DBus unique sender (e.g. :1.42) to a tracked MPRIS bus name."""
        if unique_sender in self.players:
            # sender is already a well-known name we track (unlikely but fast path)
            return unique_sender
        if not self._bus:
            return None

        # Seeked signals arrive with the unique connection name (:1.N), not the
        # well-known bus name (org.mpris.MediaPlayer2.X). Ask D-Bus which
        # well-known name owns that unique name.
        for bus_name in self.players:
            try:
                reply = await self._bus.call(
                    Message(
                        destination="org.freedesktop.DBus",
                        path="/org/freedesktop/DBus",
                        interface="org.freedesktop.DBus",
                        member="GetNameOwner",
                        signature="s",
                        body=[bus_name],
                    )
                )
                if reply and reply.body and str(reply.body[0]) == unique_sender:
                    return bus_name
            except Exception:
                continue
        return None

    async def _handle_seeked_signal(self, sender: str, position_ms: int) -> None:
        """Route Seeked signal to session using well-known bus name when possible."""
        bus_name = await self._resolve_well_known_name(sender)
        if bus_name is not None:
            self._on_seeked(bus_name, position_ms)
            return

        # If we cannot map sender reliably, force a state refresh to converge.
        await self.refresh()

    def _on_message(self, message: Message) -> bool:
        """Low-level DBus signal handler for player lifecycle/status/seek events."""
        try:
            if (
                message.interface == "org.freedesktop.DBus"
                and message.member == "NameOwnerChanged"
            ):
                # a player appeared or disappeared — rescan the full player list
                if message.body and str(message.body[0]).startswith(
                    "org.mpris.MediaPlayer2."
                ):
                    asyncio.create_task(self.refresh())
                return False

            if (
                message.interface == "org.freedesktop.DBus.Properties"
                and message.member == "PropertiesChanged"
            ):
                # message.sender is a unique connection name, not the well-known bus
                # name, so we can't filter by sender here — match by object path and
                # interface instead to scope it to MPRIS Player properties only
                path_ok = message.path == "/org/mpris/MediaPlayer2"
                iface = message.body[0] if message.body else None
                if path_ok and iface == "org.mpris.MediaPlayer2.Player":
                    asyncio.create_task(self.refresh())
                return False

            if (
                message.interface == "org.mpris.MediaPlayer2.Player"
                and message.member == "Seeked"
            ):
                sender = message.sender or ""
                if sender and message.body:
                    # MPRIS Seeked position is in microseconds; convert to ms
                    position_us = int(message.body[0])
                    asyncio.create_task(
                        self._handle_seeked_signal(
                            sender,
                            max(0, position_us // 1000),
                        )
                    )
                return False
        except Exception as e:
            logger.debug(f"PlayerMonitor signal handling error: {e}")
        return False

    async def get_position_ms(self, bus_name: str) -> Optional[int]:
        """Read player-reported position in milliseconds."""
        props = await self._get_player_props(bus_name)
        if props is None:
            return None
        try:
            position_var = await getattr(props, "call_get")(
                "org.mpris.MediaPlayer2.Player", "Position"
            )
            if position_var is None:
                return None
            return max(0, int(position_var.value) // 1000)
        except Exception as e:
            logger.debug(f"Failed to read position from {bus_name}: {e}")
            self._props_cache.pop(bus_name, None)
            return None


class ActivePlayerSelector:
    @staticmethod
    def select(
        players: dict[str, PlayerState],
        last_active: str | None,
        preferred_player: str,
    ) -> str | None:
        """Select active player by playing state, preferred keyword, and continuity."""
        if not players:
            return None
        all_names = list(players.keys())
        playing = [name for name, st in players.items() if st.status == "Playing"]
        return pick_active_player(all_names, playing, preferred_player, last_active)
