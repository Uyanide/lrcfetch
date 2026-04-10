"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 04:44:15
Description: MPRIS integration for fetching track metadata.
"""

from __future__ import annotations

import asyncio
from dbus_next.aio.message_bus import MessageBus
from dbus_next.constants import BusType
from dbus_next.message import Message
from loguru import logger
from typing import Optional, List, Any

from .config import DEFAULT_PLAYER_BLACKLIST, DEFAULT_PREFERRED_PLAYER
from .models import TrackMeta


async def _list_mpris_players(bus: MessageBus) -> List[str]:
    """List all MPRIS player bus names without any filtering."""
    try:
        reply = await bus.call(
            Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="ListNames",
            )
        )
        if not reply or not reply.body:
            return []
        return [
            name for name in reply.body[0] if name.startswith("org.mpris.MediaPlayer2.")
        ]
    except Exception as e:
        logger.error(f"Failed to list DBus names: {e}")
        return []


async def _get_playback_status(bus: MessageBus, player_name: str) -> Optional[str]:
    """Get PlaybackStatus ('Playing', 'Paused', 'Stopped') for a player."""
    try:
        introspection = await bus.introspect(player_name, "/org/mpris/MediaPlayer2")
        proxy = bus.get_proxy_object(
            player_name, "/org/mpris/MediaPlayer2", introspection
        )
        props = proxy.get_interface("org.freedesktop.DBus.Properties")
        status_var = await getattr(props, "call_get")(
            "org.mpris.MediaPlayer2.Player", "PlaybackStatus"
        )
        return status_var.value if status_var else None
    except Exception as e:
        logger.debug(f"Could not get playback status for {player_name}: {e}")
        return None


def pick_active_player(
    all_names: list[str],
    playing: list[str],
    preferred: str,
    last_active: str | None = None,
) -> str | None:
    """Select the best MPRIS player by play state, preferred keyword, and continuity.

    Priority: single playing > preferred keyword among playing > preferred keyword
    among all candidates > last active > first candidate.
    """
    if not all_names:
        return None
    if len(playing) == 1:
        return playing[0]
    candidates = playing if playing else all_names
    preferred_lower = preferred.lower().strip()
    if preferred_lower:
        for name in candidates:
            if preferred_lower in name.lower():
                return name
    if last_active and last_active in all_names:
        return last_active
    return candidates[0] if candidates else None


async def _select_player(
    bus: MessageBus,
    specific_player: Optional[str],
    preferred_player: str,
    player_blacklist: tuple[str, ...],
) -> Optional[str]:
    """Select the best MPRIS player.

    When specific_player is given, it bypasses player_blacklist and filters by name.
    Otherwise: prefer the currently playing player. If multiple are playing,
    prefer the one matching preferred_player (default: spotify).
    """
    all_names = await _list_mpris_players(bus)
    if not all_names:
        return None

    if specific_player:
        # --player bypasses player_blacklist so the user can target any player
        matched = [p for p in all_names if specific_player.lower() in p.lower()]
        return matched[0] if matched else None

    # auto-selection: apply blacklist before choosing
    candidates = [
        p
        for p in all_names
        if not any(x.lower() in p.lower() for x in player_blacklist)
    ]
    playing: list[str] = []
    for p in candidates:
        status = await _get_playback_status(bus, p)
        logger.debug(f"Player {p}: {status}")
        if status == "Playing":
            playing.append(p)

    return pick_active_player(candidates, playing, preferred_player)


async def _fetch_metadata_dbus(
    specific_player: Optional[str],
    preferred_player: str,
    player_blacklist: tuple[str, ...],
) -> Optional[TrackMeta]:
    bus = None
    try:
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
    except Exception as e:
        logger.error(f"Failed to connect to DBus: {e}")
        return None

    try:
        player_name = await _select_player(
            bus, specific_player, preferred_player, player_blacklist
        )
        if not player_name:
            logger.debug(
                f"No active MPRIS players found via DBus{' for ' + specific_player if specific_player else ''}."
            )
            return None

        logger.debug(f"Using player: {player_name}")

        introspection = await bus.introspect(player_name, "/org/mpris/MediaPlayer2")
        proxy = bus.get_proxy_object(
            player_name, "/org/mpris/MediaPlayer2", introspection
        )

        props_iface = proxy.get_interface("org.freedesktop.DBus.Properties")
        if not props_iface:
            logger.error(f"Player {player_name} doesn't support Properties interface.")
            return None

        try:
            metadata_var: Any = await getattr(props_iface, "call_get")(
                "org.mpris.MediaPlayer2.Player", "Metadata"
            )
            if not metadata_var:
                logger.error("Empty metadata received.")
                return None

            metadata = metadata_var.value

            # Extract trackid — MPRIS returns either "spotify:track:ID"
            # or a DBus object path like "/com/spotify/track/ID"
            trackid = metadata.get("mpris:trackid", None)
            if trackid:
                trackid = trackid.value
                if isinstance(trackid, str):
                    if trackid.startswith("spotify:track:"):
                        trackid = trackid.removeprefix("spotify:track:")
                    elif trackid.startswith("/com/spotify/track/"):
                        trackid = trackid.removeprefix("/com/spotify/track/")
                    else:
                        trackid = None

            # Extract length (usually microseconds)
            length = metadata.get("mpris:length", None)
            if length:
                length = length.value // 1000 if isinstance(length.value, int) else None

            album = metadata.get("xesam:album", None)
            album = album.value if album else None

            artist = metadata.get("xesam:artist", None)
            artist = (
                artist.value[0]
                if artist and isinstance(artist.value, list) and artist.value
                else None
            )

            title = metadata.get("xesam:title", None)
            title = title.value if title else None

            url = metadata.get("xesam:url", None)
            url = url.value if url else None

            return TrackMeta(
                trackid=trackid,
                length=length,
                album=album,
                artist=artist,
                title=title,
                url=url,
            )
        except Exception as e:
            logger.error(f"Failed to get properties from {player_name}: {e}")
            return None

    finally:
        if bus:
            bus.disconnect()


def get_current_track(
    player_name: Optional[str] = None,
    preferred_player: str = DEFAULT_PREFERRED_PLAYER,
    player_blacklist: tuple[str, ...] = DEFAULT_PLAYER_BLACKLIST,
) -> Optional[TrackMeta]:
    try:
        return asyncio.run(
            _fetch_metadata_dbus(player_name, preferred_player, player_blacklist)
        )
    except Exception as e:
        logger.error(f"DBus async loop failed: {e}")
        return None
