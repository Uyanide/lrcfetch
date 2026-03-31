"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-26 02:04:39
Description: CLI interface
"""

import sys
import time
import os
from typing import Annotated
import cyclopts
from loguru import logger

from .config import enable_debug
from .models import TrackMeta, CacheStatus
from .mpris import get_current_track
from .core import LrcManager, FetcherMethodType
from .lrc import get_sidecar_path


app = cyclopts.App(
    help="LRCFetch — Fetch line-synced lyrics for your music player.",
)
app.register_install_completion_command()

cache_app = cyclopts.App(name="cache", help="Manage the local SQLite cache.")
app.command(cache_app)

manager = LrcManager()

# Global state set by the meta launcher
_player: str | None = None


@app.meta.default
def launcher(
    *tokens: Annotated[str, cyclopts.Parameter(show=False, allow_leading_hyphen=True)],
    debug: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--debug", "-d"], negative="", help="Enable debug logging."
        ),
    ] = False,
    player: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--player", "-p"],
            help="Target a specific MPRIS player using its DBus name or a portion thereof.",
        ),
    ] = None,
):
    global _player
    if debug:
        enable_debug()
    _player = player
    app(tokens)


# fetch


@app.command
def fetch(
    *,
    method: Annotated[
        FetcherMethodType | None,
        cyclopts.Parameter(help="Force a specific source."),
    ] = None,
    no_cache: Annotated[
        bool,
        cyclopts.Parameter(
            name="--no-cache", negative="", help="Bypass the cache for this request."
        ),
    ] = False,
    only_synced: Annotated[
        bool,
        cyclopts.Parameter(
            name="--only-synced", negative="", help="Only accept synced (timed) lyrics."
        ),
    ] = False,
):
    """Fetch and print lyrics for the currently playing track."""
    track = get_current_track(_player)

    if not track:
        logger.error("No active playing track found.")
        sys.exit(1)

    logger.info(f"Track: {track.display_name()}")

    result = manager.fetch_for_track(track, force_method=method, bypass_cache=no_cache)

    if not result or not result.lyrics:
        logger.error("No lyrics found.")
        sys.exit(1)

    if only_synced and result.status != CacheStatus.SUCCESS_SYNCED:
        logger.error("Only unsynced lyrics available (--only-synced requested).")
        sys.exit(1)

    print(result.lyrics)


# search


@app.command
def search(
    *,
    title: Annotated[
        str, cyclopts.Parameter(name=["--title", "-t"], help="Track title.")
    ],
    artist: Annotated[
        str | None, cyclopts.Parameter(name=["--artist", "-a"], help="Artist name.")
    ] = None,
    album: Annotated[str | None, cyclopts.Parameter(help="Album name.")] = None,
    trackid: Annotated[str | None, cyclopts.Parameter(help="Spotify track ID.")] = None,
    length: Annotated[
        int | None,
        cyclopts.Parameter(
            name=["--length", "-l"], help="Track duration in milliseconds."
        ),
    ] = None,
    url: Annotated[
        str | None, cyclopts.Parameter(help="Local file URL (file:///...).")
    ] = None,
    method: Annotated[
        FetcherMethodType | None, cyclopts.Parameter(help="Force a specific source.")
    ] = None,
    no_cache: Annotated[
        bool,
        cyclopts.Parameter(
            name="--no-cache", negative="", help="Bypass the cache for this request."
        ),
    ] = False,
    only_synced: Annotated[
        bool,
        cyclopts.Parameter(
            name="--only-synced", negative="", help="Only accept synced (timed) lyrics."
        ),
    ] = False,
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

    result = manager.fetch_for_track(track, force_method=method, bypass_cache=no_cache)

    if not result or not result.lyrics:
        logger.error("No lyrics found.")
        sys.exit(1)

    if only_synced and result.status != CacheStatus.SUCCESS_SYNCED:
        logger.error("Only unsynced lyrics available (--only-synced requested).")
        sys.exit(1)

    print(result.lyrics)


# export


@app.command
def export(
    *,
    output: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--output", "-o"],
            help="Output file path (default: same directory as audio file with .lrc extension, or current directory if not available).",
        ),
    ] = None,
    method: Annotated[
        FetcherMethodType | None, cyclopts.Parameter(help="Force a specific source.")
    ] = None,
    no_cache: Annotated[
        bool, cyclopts.Parameter(name="--no-cache", negative="", help="Bypass cache.")
    ] = False,
    overwrite: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--overwrite", "-f"], negative="", help="Overwrite existing file."
        ),
    ] = False,
):
    """Export lyrics of the current track to a .lrc file."""
    track = get_current_track(_player)
    if not track:
        logger.error("No active playing track found.")
        sys.exit(1)

    result = manager.fetch_for_track(track, force_method=method, bypass_cache=no_cache)
    if not result or not result.lyrics:
        logger.error("No lyrics available to export.")
        sys.exit(1)

    # Build default output path
    if not output:
        if track.url:
            lrc_path = get_sidecar_path(track.url, ensure_exists=False)
            if lrc_path:
                output = str(lrc_path)
                logger.info(f"Exporting to sidecar path: {output}")

    # Fallback to current directory with sanitized filename
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
        sys.exit(1)

    try:
        with open(output, "w", encoding="utf-8") as f:
            f.write(result.lyrics)
        logger.info(f"Exported lyrics to {output}")
    except Exception as e:
        logger.error(f"Failed to write file: {e}")
        sys.exit(1)


# cache subcommands


@cache_app.command
def query(
    *,
    all: Annotated[
        bool,
        cyclopts.Parameter(name="--all", negative="", help="Dump all cache entries."),
    ] = False,
):
    """Show cached entries for the current track."""
    if all:
        rows = manager.cache.query_all()
        if not rows:
            print("Cache is empty.")
            return
        for row in rows:
            _print_cache_row(row)
            print()
        return

    track = get_current_track(_player)
    if not track:
        logger.error("No active playing track found.")
        sys.exit(1)
    _print_track_cache(track)


@cache_app.command
def clear(
    *,
    all: Annotated[
        bool,
        cyclopts.Parameter(name="--all", negative="", help="Clear the entire cache."),
    ] = False,
):
    """Clear cached entries for the current track."""
    if all:
        manager.cache.clear_all()
        return

    track = get_current_track(_player)
    if not track:
        logger.error("No active playing track found.")
        sys.exit(1)
    manager.cache.clear_track(track)


@cache_app.command
def prune():
    """Remove expired cache entries."""
    manager.cache.prune()


@cache_app.command
def stats():
    """Show cache statistics."""
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


# helpers


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
            print(
                f"{indent}  Expires : in {remaining // 3600}h {(remaining % 3600) // 60}m"
            )
        else:
            print(f"{indent}  Expires : EXPIRED")
    else:
        print(f"{indent}  Expires : never")
    if lyrics:
        line_count = len(lyrics.splitlines())
        print(f"{indent}  Lyrics  : {line_count} lines")


def run():
    app.meta()


if __name__ == "__main__":
    run()
