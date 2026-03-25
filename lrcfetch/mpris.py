import asyncio
from dbus_next.aio.message_bus import MessageBus
from dbus_next.constants import BusType
from dbus_next.message import Message
from lrcfetch.models import TrackMeta
from loguru import logger
from typing import Optional, List, Any
import subprocess

async def _get_active_players(bus: MessageBus, specific_player: Optional[str] = None) -> List[str]:
    try:
        reply = await bus.call(
            Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="ListNames"
            )
        )
        if not reply or not reply.body:
            return []
            
        names = reply.body[0]
        players = [name for name in names if name.startswith("org.mpris.MediaPlayer2.")]
        
        if specific_player:
            players = [p for p in players if specific_player.lower() in p.lower()]
        else:
            # Sort so that spotify is preferred
            players.sort(key=lambda x: 0 if "spotify" in x.lower() else 1)
            
        return players
    except Exception as e:
        logger.error(f"Failed to list DBus names: {e}")
        return []

async def _fetch_metadata_dbus(specific_player: Optional[str] = None) -> Optional[TrackMeta]:
    bus = None
    try:
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
    except Exception as e:
        logger.error(f"Failed to connect to DBus: {e}")
        return None

    try:
        players = await _get_active_players(bus, specific_player)
        if not players:
            logger.debug(f"No active MPRIS players found via DBus{' for ' + specific_player if specific_player else ''}.")
            return None

        player_name = players[0]
        logger.debug(f"Using player: {player_name}")

        introspection = await bus.introspect(player_name, "/org/mpris/MediaPlayer2")
        proxy = bus.get_proxy_object(player_name, "/org/mpris/MediaPlayer2", introspection)
        
        props_iface = proxy.get_interface("org.freedesktop.DBus.Properties")
        if not props_iface:
            logger.error(f"Player {player_name} doesn't support Properties interface.")
            return None
            
        try:
            metadata_var: Any = await getattr(props_iface, "call_get")("org.mpris.MediaPlayer2.Player", "Metadata")
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
            
            # Extract length (usually microseconds)
            length = metadata.get("mpris:length", None)
            if length:
                length = length.value // 1000 if isinstance(length.value, int) else None
                
            album = metadata.get("xesam:album", None)
            album = album.value if album else None
            
            artist = metadata.get("xesam:artist", None)
            artist = artist.value[0] if artist and isinstance(artist.value, list) and artist.value else None
            
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
                url=url
            )
        except Exception as e:
            logger.error(f"Failed to get properties from {player_name}: {e}")
            return None
            
    finally:
        if bus:
            bus.disconnect()

def _fetch_metadata_subprocess(specific_player: Optional[str] = None) -> Optional[TrackMeta]:
    """Fallback using playerctl if dbus-next fails or session bus is problematic."""
    logger.debug("Attempting to use playerctl as fallback.")
    try:
        # Check if playerctl exists
        subprocess.run(["playerctl", "--version"], capture_output=True, check=True)
        
        base_cmd = ["playerctl"]
        if specific_player:
            base_cmd.extend(["-p", specific_player])
            
        def _get_prop(prop: str) -> Optional[str]:
            res = subprocess.run(base_cmd + ["metadata", prop], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
            return None
            
        trackid = _get_prop("mpris:trackid")
        if trackid:
            if trackid.startswith("spotify:track:"):
                trackid = trackid.removeprefix("spotify:track:")
            elif trackid.startswith("/com/spotify/track/"):
                trackid = trackid.removeprefix("/com/spotify/track/")
            
        length_str = _get_prop("mpris:length")
        length = int(length_str) // 1000 if length_str and length_str.isdigit() else None
        
        album = _get_prop("xesam:album")
        artist = _get_prop("xesam:artist")
        title = _get_prop("xesam:title")
        url = _get_prop("xesam:url")
        
        if not any([trackid, length, album, artist, title, url]):
            return None
            
        return TrackMeta(
            trackid=trackid,
            length=length,
            album=album,
            artist=artist,
            title=title,
            url=url
        )
    except Exception as e:
        logger.debug(f"playerctl fallback failed: {e}")
        return None

def get_current_track(player_name: Optional[str] = None) -> Optional[TrackMeta]:
    try:
        meta = asyncio.run(_fetch_metadata_dbus(player_name))
        if meta:
            return meta
    except Exception as e:
        logger.error(f"DBus async loop failed: {e}")
        
    return _fetch_metadata_subprocess(player_name)
