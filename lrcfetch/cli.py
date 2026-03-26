"""CLI interface for lrcfetch."""

import typer
import time
from typing import Optional
from loguru import logger
import os

from lrcfetch.config import enable_debug
from lrcfetch.models import TrackMeta, CacheStatus
from lrcfetch.mpris import get_current_track
from lrcfetch.core import LrcManager

app = typer.Typer(
    help="LRCFetch — Fetch line-synced lyrics for your music player.",
    add_completion=True,
)

manager = LrcManager()

# Global state set by the app callback
_player: Optional[str] = None


@app.callback()
def main(
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug logging."),
    player: Optional[str] = typer.Option(
        None, "--player", "-p", help="Target a specific MPRIS player using its DBus name or a portion thereof."
    ),
):
    global _player
    if debug:
        enable_debug()
    _player = player


# ------------------------------------------------------------------
# fetch
# ------------------------------------------------------------------


@app.command()
def fetch(
    method: Optional[str] = typer.Option(
        None, "--method", help="Force a specific source (local, spotify, lrclib, lrclib-search, netease)."
    ),
    no_cache: bool = typer.Option(
        False, "--no-cache", help="Bypass the cache for this request."
    ),
    only_synced: bool = typer.Option(
        False, "--only-synced", help="Only accept synced (timed) lyrics."
    ),
):
    """Fetch and print lyrics for the currently playing track."""
    track = get_current_track(_player)

    if not track:
        logger.error("No active playing track found.")
        raise typer.Exit(1)

    logger.info(f"Track: {track.display_name()}")

    result = manager.fetch_for_track(
        track, force_method=method, bypass_cache=no_cache
    )

    if not result or not result.lyrics:
        logger.error("No lyrics found.")
        raise typer.Exit(1)

    if only_synced and result.status != CacheStatus.SUCCESS_SYNCED:
        logger.error("Only unsynced lyrics available (--only-synced requested).")
        raise typer.Exit(1)

    print(result.lyrics)


# ------------------------------------------------------------------
# search
# ------------------------------------------------------------------


@app.command()
def search(
    title: str = typer.Option(..., "--title", "-t", help="Track title."),
    artist: Optional[str] = typer.Option(None, "--artist", "-a", help="Artist name."),
    album: Optional[str] = typer.Option(None, "--album", help="Album name."),
    trackid: Optional[str] = typer.Option(None, "--trackid", help="Spotify track ID."),
    length: Optional[int] = typer.Option(None, "--length", "-l", help="Track duration in milliseconds."),
    url: Optional[str] = typer.Option(None, "--url", help="Local file URL (file:///...)."),
    method: Optional[str] = typer.Option(
        None, "--method", help="Force a specific source."
    ),
    no_cache: bool = typer.Option(
        False, "--no-cache", help="Bypass the cache for this request."
    ),
    only_synced: bool = typer.Option(
        False, "--only-synced", help="Only accept synced (timed) lyrics."
    ),
):
    """Search for lyrics by metadata (bypasses MPRIS)."""
    track = TrackMeta(
        title=title,
        artist=artist,
        album=album,
        trackid=trackid,
        length=length,
        url=url,
    )

    logger.info(f"Track: {track.display_name()}")

    result = manager.fetch_for_track(
        track, force_method=method, bypass_cache=no_cache
    )

    if not result or not result.lyrics:
        logger.error("No lyrics found.")
        raise typer.Exit(1)

    if only_synced and result.status != CacheStatus.SUCCESS_SYNCED:
        logger.error("Only unsynced lyrics available (--only-synced requested).")
        raise typer.Exit(1)

    print(result.lyrics)


# ------------------------------------------------------------------
# export
# ------------------------------------------------------------------


@app.command()
def export(
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Output file path (default: <Artist> - <Title>.lrc)."
    ),
    method: Optional[str] = typer.Option(
        None, "--method", help="Force a specific source."
    ),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass cache."),
    overwrite: bool = typer.Option(
        False, "--overwrite", "-f", help="Overwrite existing file."
    ),
):
    """Export lyrics of the current track to a .lrc file."""
    track = get_current_track(_player)
    if not track:
        logger.error("No active playing track found.")
        raise typer.Exit(1)

    result = manager.fetch_for_track(
        track, force_method=method, bypass_cache=no_cache
    )
    if not result or not result.lyrics:
        logger.error("No lyrics available to export.")
        raise typer.Exit(1)

    # Build default output path
    if not output:
        filename = (
            f"{track.artist} - {track.title}.lrc"
            if track.artist and track.title
            else "lyrics.lrc"
        )
        # Sanitize filename
        filename = "".join(
            c for c in filename if c.isalpha() or c.isdigit() or c in " -_."
        ).rstrip()
        output = os.path.join(os.getcwd(), filename)

    if os.path.exists(output) and not overwrite:
        logger.error(f"File exists: {output}  (use -f to overwrite)")
        raise typer.Exit(1)

    try:
        with open(output, "w", encoding="utf-8") as f:
            f.write(result.lyrics)
        logger.info(f"Exported lyrics to {output}")
    except Exception as e:
        logger.error(f"Failed to write file: {e}")
        raise typer.Exit(1)


# ------------------------------------------------------------------
# cache
# ------------------------------------------------------------------


@app.command()
def cache(
    clear: bool = typer.Option(False, "--clear", help="Clear the entire cache."),
    clear_current: bool = typer.Option(
        False, "--clear-current", help="Clear cache for the current track."
    ),
    prune: bool = typer.Option(False, "--prune", help="Remove expired entries."),
    stats: bool = typer.Option(False, "--stats", help="Show cache statistics."),
    query: bool = typer.Option(
        False, "--query", "-q", help="Show detailed cache info for the current track."
    ),
    query_all: bool = typer.Option(
        False, "--query-all", help="Dump all cache entries."
    ),
):
    """Manage the local SQLite cache."""
    if clear:
        manager.cache.clear_all()
        return

    if clear_current:
        track = get_current_track(_player)
        if not track:
            logger.error("No active playing track found.")
            raise typer.Exit(1)
        manager.cache.clear_track(track)
        return

    if prune:
        manager.cache.prune()
        return

    if stats:
        s = manager.cache.stats()
        print("=== Cache Statistics ===")
        print(f"Total entries : {s['total']}")
        print(f"Active        : {s['active']}")
        print(f"Expired       : {s['expired']}")
        if s["by_status"]:
            print("\nBy status:")
            for status, count in s["by_status"].items():
                print(f"  {status}: {count}")
        if s["by_source"]:
            print("\nBy source:")
            for source, count in s["by_source"].items():
                print(f"  {source}: {count}")
        return

    if query:
        track = get_current_track(_player)
        if not track:
            logger.error("No active playing track found.")
            raise typer.Exit(1)
        _print_track_cache(track)
        return

    if query_all:
        rows = manager.cache.query_all()
        if not rows:
            print("Cache is empty.")
            return
        for row in rows:
            _print_cache_row(row)
            print()
        return

    logger.info(
        "No action specified. Try --stats, --query, --query-all, "
        "--prune, --clear, or --clear-current."
    )


def _print_track_cache(track: TrackMeta) -> None:
    """Print all cached entries for a given track."""
    print(f"Track: {track.display_name()}")
    if track.album:
        print(f"Album: {track.album}")
    if track.length:
        secs = track.length / 1000.0
        print(f"Duration: {int(secs // 60)}:{secs % 60:05.2f}")
    print()

    rows = manager.cache.query_track(track)
    if not rows:
        print("  (no cache entries)")
        return

    for row in rows:
        _print_cache_row(row, indent="  ")


def _print_cache_row(row: dict, indent: str = "") -> None:
    """Pretty-print a single cache row."""
    now = int(time.time())
    source = row.get("source", "?")
    status = row.get("status", "?")
    artist = row.get("artist", "")
    title = row.get("title", "")
    album = row.get("album", "")
    created = row.get("created_at", 0)
    expires = row.get("expires_at")
    lyrics = row.get("lyrics", "")

    name = f"{artist} - {title}" if artist and title else row.get("key", "?")
    print(f"{indent}[{source}] {name}")
    if album:
        print(f"{indent}  Album   : {album}")
    print(f"{indent}  Status  : {status}")
    if created:
        age = now - created
        print(f"{indent}  Cached  : {age // 3600}h {(age % 3600) // 60}m ago")
    if expires:
        remaining = expires - now
        if remaining > 0:
            print(f"{indent}  Expires : in {remaining // 3600}h {(remaining % 3600) // 60}m")
        else:
            print(f"{indent}  Expires : EXPIRED")
    else:
        print(f"{indent}  Expires : never")
    if lyrics:
        line_count = len(lyrics.splitlines())
        print(f"{indent}  Lyrics  : {line_count} lines")


def run():
    app()


if __name__ == "__main__":
    run()
