"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-26 02:04:39
Description: CLI interface.
"""

import sys
import time
import os
import asyncio
import json
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
import cyclopts
from loguru import logger

from .config import (
    DB_PATH,
    AppConfig,
    load_config,
    enable_debug,
)
from .models import TrackMeta
from .mpris import get_current_track
from .core import LrcManager
from .fetchers import FetcherMethodType
from .lrc import get_sidecar_path
from .watch import WatchCoordinator
from .watch.control import ControlClient, parse_delta
from .watch.view.pipe import PipeOutput
from .watch.view.print import PrintOutput


app = cyclopts.App(
    help="LRX-CLI — Fetch line-synced lyrics for your music player.",
)
app.register_install_completion_command()

cache_app = cyclopts.App(name="cache", help="Manage the local SQLite cache.")
app.command(cache_app)

watch_app = cyclopts.App(name="watch", help="Watch MPRIS and output lyrics.")
app.command(watch_app)

ctl_app = cyclopts.App(name="ctl", help="Control a running watch session.")
watch_app.command(ctl_app)


# Global state set by the meta launcher
_player: str | None = None
_db_path: str | None = None
_app_config: AppConfig = AppConfig()

# Will be initialized before any command runs, safe to set to None here
manager: LrcManager = None  # type: ignore


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
    db_path: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--db-path", "-c"],
            help=f"Custom path for the cache database file (default: {DB_PATH}).",
        ),
    ] = None,
):
    global _player, _db_path, _app_config, manager
    if debug:
        enable_debug()
    _player = player
    _db_path = str(Path(db_path).resolve()) if db_path else DB_PATH
    _app_config = load_config()
    manager = LrcManager(db_path=_db_path, config=_app_config)
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
    allow_unsynced: Annotated[
        bool,
        cyclopts.Parameter(
            name="--allow-unsynced",
            negative="",
            help="Allow unsynced lyrics (will be displayed with all time tags set to [00:00.00]).",
        ),
    ] = False,
    plain: Annotated[
        bool,
        cyclopts.Parameter(
            name="--plain",
            negative="",
            help="Output only plain lyrics without tags (highest priority over --normalize).",
        ),
    ] = False,
    normalize: Annotated[
        bool,
        cyclopts.Parameter(
            name="--normalize",
            negative="",
            help="Output normalized LRC (ignored when --plain is also set).",
        ),
    ] = False,
):
    """Fetch and print lyrics for the currently playing track."""
    track = get_current_track(
        _player,
        preferred_player=_app_config.general.preferred_player,
        player_blacklist=_app_config.general.player_blacklist,
    )

    if not track:
        logger.error("No active playing track found.")
        sys.exit(1)

    logger.info(f"Track: {track.display_name()}")

    result = manager.fetch_for_track(
        track,
        force_method=method,
        bypass_cache=no_cache,
        allow_unsynced=allow_unsynced,
    )

    if not result or not result.lyrics:
        logger.error("No lyrics found.")
        sys.exit(1)

    if plain:
        print(result.lyrics.to_plain())
    elif normalize:
        print(result.lyrics.to_normalized_text())
    else:
        print(result.lyrics.to_text())


# search


@app.command
def search(
    *,
    title: Annotated[
        str | None, cyclopts.Parameter(name=["--title", "-t"], help="Track title.")
    ] = None,
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
        str | None,
        cyclopts.Parameter(
            help="Local file URL (file:///...). Mutually exclusive with --path."
        ),
    ] = None,
    path: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--path"],
            help="Local audio file path. Mutually exclusive with --url.",
        ),
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
    allow_unsynced: Annotated[
        bool,
        cyclopts.Parameter(
            name="--allow-unsynced",
            negative="",
            help="Allow unsynced lyrics (will be displayed with all time tags set to [00:00.00]).",
        ),
    ] = False,
    plain: Annotated[
        bool,
        cyclopts.Parameter(
            name="--plain",
            negative="",
            help="Output only plain lyrics without tags (highest priority over --normalize).",
        ),
    ] = False,
    normalize: Annotated[
        bool,
        cyclopts.Parameter(
            name="--normalize",
            negative="",
            help="Output normalized LRC (ignored when --plain is also set).",
        ),
    ] = False,
):
    """Search for lyrics by metadata (bypasses MPRIS)."""
    if url and path:
        logger.error("--url and --path are mutually exclusive.")
        sys.exit(1)

    if path:
        resolved = str(Path(path).resolve())
        url = "file://" + quote(resolved, safe="/")

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
        track,
        force_method=method,
        bypass_cache=no_cache,
        allow_unsynced=allow_unsynced,
    )

    if not result or not result.lyrics:
        logger.error("No lyrics found.")
        sys.exit(1)

    if plain:
        print(result.lyrics.to_plain())
    elif normalize:
        print(result.lyrics.to_normalized_text())
    else:
        print(result.lyrics.to_text())


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
    allow_unsynced: Annotated[
        bool,
        cyclopts.Parameter(
            name="--allow-unsynced",
            negative="",
            help="Allow unsynced lyrics (will be exported with all time tags set to [00:00.00] if --plain is not present).",
        ),
    ] = False,
    plain: Annotated[
        bool,
        cyclopts.Parameter(
            name="--plain",
            negative="",
            help="Export only plain lyrics (.txt, highest priority over --normalize).",
        ),
    ] = False,
    normalize: Annotated[
        bool,
        cyclopts.Parameter(
            name="--normalize",
            negative="",
            help="Export normalized LRC output (ignored when --plain is also set).",
        ),
    ] = False,
):
    """Export lyrics of the current track to a .lrc file."""
    track = get_current_track(
        _player,
        preferred_player=_app_config.general.preferred_player,
        player_blacklist=_app_config.general.player_blacklist,
    )
    if not track:
        logger.error("No active playing track found.")
        sys.exit(1)

    result = manager.fetch_for_track(
        track,
        force_method=method,
        bypass_cache=no_cache,
        allow_unsynced=allow_unsynced,
    )
    if not result or not result.lyrics:
        logger.error("No lyrics available to export.")
        sys.exit(1)

    # Output file extension
    ext = ".lrc" if not plain else ".txt"
    if output and not output.endswith(ext):
        output += ext

    # Build default output path
    if not output:
        if track.url:
            lrc_path = get_sidecar_path(track.url, ensure_exists=False, extension=ext)
            if lrc_path:
                output = str(lrc_path)
                logger.info(f"Exporting to sidecar path: {output}")

    # Fallback to current directory with sanitized filename
    if not output:
        filename = (
            f"{track.artist} - {track.title}{ext}"
            if track.artist and track.title
            else "lyrics" + ext
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
            if plain:
                f.write(result.lyrics.to_plain())
            elif normalize:
                f.write(result.lyrics.to_normalized_text())
            else:
                f.write(result.lyrics.to_text())
        logger.info(f"Exported lyrics to {output}")
    except Exception as e:
        logger.error(f"Failed to write file: {e}")
        sys.exit(1)


# watch subcommands


@watch_app.command
def pipe(
    before: Annotated[
        int,
        cyclopts.Parameter(
            name=["--before", "-b"],
            help="Number of lyric lines to show before current line.",
        ),
    ] = 0,
    after: Annotated[
        int,
        cyclopts.Parameter(
            name=["--after", "-a"],
            help="Number of lyric lines to show after current line.",
        ),
    ] = 0,
    no_newline: Annotated[
        bool,
        cyclopts.Parameter(
            name=["--no-newline", "-n"],
            negative="",
            help="Do not append a new line after the lyric output.",
        ),
    ] = False,
):
    """Watch active player and continuously print lyric window to stdout."""
    logger.info(
        "Starting watch pipe (player filter: {})",
        _player or "<none>",
    )
    output = PipeOutput(
        before=max(0, before), after=max(0, after), no_newline=no_newline
    )
    try:
        session = WatchCoordinator(
            manager,
            output,
            player_hint=_player,
            config=_app_config,
        )
        success = asyncio.run(session.run())
        if not success:
            sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Watch stopped.")


@watch_app.command(name="print")
def watch_print(
    plain: Annotated[
        bool,
        cyclopts.Parameter(
            name="--plain",
            negative="",
            help="Output plain text (strips all tags). Takes priority over --normalize.",
        ),
    ] = False,
) -> None:
    """Watch active player and print all lyrics to stdout once per track change."""
    logger.info(
        "Starting watch print (player filter: {})",
        _player or "<none>",
    )
    output = PrintOutput(plain=plain)
    try:
        session = WatchCoordinator(
            manager,
            output,
            player_hint=_player,
            config=_app_config,
        )
        success = asyncio.run(session.run())
        if not success:
            sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Watch stopped.")


@ctl_app.command
def offset(delta: str) -> None:
    """Adjust watch offset. Examples: +200, -200, 0."""
    parsed_ok, parsed_delta, parse_error = parse_delta(delta)
    if not parsed_ok or parsed_delta is None:
        logger.error(parse_error or "Invalid offset delta")
        sys.exit(1)

    response = ControlClient(_app_config.watch.socket_path).send(
        {"cmd": "offset", "delta": parsed_delta}
    )
    if not response.get("ok"):
        logger.error(response.get("error", "Unknown error"))
        sys.exit(1)
    print(json.dumps(response, indent=2, ensure_ascii=False))


@ctl_app.command
def status() -> None:
    """Print current watch session status as JSON."""
    response = ControlClient(_app_config.watch.socket_path).send({"cmd": "status"})
    if not response.get("ok"):
        logger.error(response.get("error", "Unknown error"))
        sys.exit(1)
    print(json.dumps(response, indent=2, ensure_ascii=False))


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

    track = get_current_track(
        _player,
        preferred_player=_app_config.general.preferred_player,
        player_blacklist=_app_config.general.player_blacklist,
    )
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

    track = get_current_track(
        _player,
        preferred_player=_app_config.general.preferred_player,
        player_blacklist=_app_config.general.player_blacklist,
    )
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

    by_slot = s.get("by_slot", {})
    if by_slot:
        print(
            "Slots         : "
            + ", ".join(f"{k}={v}" for k, v in sorted(by_slot.items()))
        )

    # Source × Status table
    table = s.get("source_status", {})
    if table:
        all_statuses = sorted({st for row in table.values() for st in row})
        # Short labels for column headers
        short = {
            "SUCCESS_SYNCED": "synced",
            "SUCCESS_UNSYNCED": "unsynced",
            "NOT_FOUND": "not_found",
            "NETWORK_ERROR": "net_err",
        }
        headers = [short.get(st, st) for st in all_statuses]
        sources = sorted(table.keys())
        # Column widths
        src_w = max(len(src) for src in sources)
        src_w = max(src_w, 6)  # min width for "source" header
        col_w = [max(len(h) if h else 0, 4) for h in headers]

        print(
            f"\n{'source':<{src_w}}  "
            + "  ".join(f"{h:>{w}}" for h, w in zip(headers, col_w))
        )
        print("-" * src_w + "  " + "  ".join("-" * w for w in col_w))
        for src in sources:
            counts = [str(table[src].get(st, 0)) for st in all_statuses]
            print(
                f"{src:<{src_w}}  "
                + "  ".join(f"{c:>{w}}" for c, w in zip(counts, col_w))
            )
        totals = [
            str(sum(table[src].get(st, 0) for src in sources)) for st in all_statuses
        ]
        print("-" * src_w + "  " + "  ".join("-" * w for w in col_w))
        print(
            f"{'total':<{src_w}}  "
            + "  ".join(f"{c:>{w}}" for c, w in zip(totals, col_w))
        )

    # Confidence distribution (positive entries only)
    buckets = s.get("confidence_buckets", {})
    non_empty = {k: v for k, v in buckets.items() if v > 0}
    if non_empty:
        label_w = max(len(k) for k in non_empty)
        print("\nConfidence distribution (positive entries):")
        for label, count in buckets.items():
            if count > 0:
                print(f"  {label:>{label_w}} : {count}")


@cache_app.command
def confidence(
    source: Annotated[
        str, cyclopts.Parameter(help="Source to update (e.g. spotify, netease).")
    ],
    score: Annotated[float, cyclopts.Parameter(help="Confidence score (0-100).")],
):
    """Set confidence score for the current track's cache entry from a specific source."""
    if not 0 <= score <= 100:
        logger.error("Score must be between 0 and 100.")
        sys.exit(1)

    track = get_current_track(
        _player,
        preferred_player=_app_config.general.preferred_player,
        player_blacklist=_app_config.general.player_blacklist,
    )
    if not track:
        logger.error("No active playing track found.")
        sys.exit(1)

    updated = manager.cache.update_confidence(track, score, source=source)
    if updated:
        print(f"Updated [{source}] confidence to {score:.0f}.")
    else:
        print(f"No cache entry found for [{source}].")


@cache_app.command
def insert(
    *,
    path: Annotated[
        str | None,
        cyclopts.Parameter(
            name=["--path"],
            help="Path to a local .lrc file to insert instead of reading from stdin.",
        ),
    ] = None,
):
    """Manually insert lyrics into the cache for the current track."""
    track = get_current_track(
        _player,
        preferred_player=_app_config.general.preferred_player,
        player_blacklist=_app_config.general.player_blacklist,
    )
    if not track:
        logger.error("No active playing track found.")
        sys.exit(1)

    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                lyrics = f.read()
        except Exception as e:
            logger.error(f"Failed to read file: {e}")
            sys.exit(1)
    else:
        logger.info("Reading lyrics from stdin (Ctrl+D to finish)...")
        lyrics = sys.stdin.read()

    manager.manual_insert(track, lyrics)


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
    slot = row.get("positive_kind", "?")
    status = row.get("status", "?")
    artist = row.get("artist", "")
    title = row.get("title", "")
    album = row.get("album", "")
    created = row.get("created_at", 0)
    expires = row.get("expires_at")
    lyrics = row.get("lyrics", "")
    confidence = row.get("confidence")

    name = f"{artist} - {title}" if artist and title else row.get("key", "?")
    print(f"{indent}[{source}/{slot}] {name}")
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
    if confidence is not None:
        print(f"{indent}  Confidence: {confidence:.0f}")
    else:
        print(f"{indent}  Confidence: (legacy)")


def run():
    app.meta()


if __name__ == "__main__":
    run()
