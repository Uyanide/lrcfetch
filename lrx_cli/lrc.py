"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 21:54:01
Description: Shared LRC time-tag utilities (definitely overengineered)
"""

import re
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from .models import CacheStatus

# Parses any time tag input format:
#   [mm:ss], [mm:ss.c], [mm:ss.cc], [mm:ss.ccc], [mm:ss:cc], …
_RAW_TAG_RE = re.compile(r"\[(\d{2,}):(\d{2})(?:[.:](\d{1,3}))?\]")

# Standard format after normalization: [mm:ss.cc]
_STD_TAG_RE = re.compile(r"\[\d{2,}:\d{2}\.\d{2}\]")

# Standard format with capture groups
_STD_TAG_CAPTURE_RE = re.compile(r"\[(\d{2,}):(\d{2})\.(\d{2})\]")

# [offset:+/-xxx] tag — value in milliseconds
_OFFSET_RE = re.compile(r"^\[offset:\s*([+-]?\d+)\]\s*$", re.MULTILINE | re.IGNORECASE)

# Any number of ID/Time tags at the start of a line
_LINE_START_TAGS_RE = re.compile(r"^(?:\[[^\]]*\])+", re.MULTILINE)

# Any number of standard time tags at the start of a line
_LINE_START_STD_TAGS_RE = re.compile(r"^(?:\[\d{2,}:\d{2}\.\d{2}\])+", re.MULTILINE)

# Word-level sync tags
#   <mm:ss>, <mm:ss.c>, <mm:ss.cc>, <mm:ss:cc>, <xx,yy,zz>
_WORD_SYNC_TAG_RE = re.compile(r"<\d{2,}:\d{2}(?:[.:]\d{1,3})?>|<\d+,\d+,\d+>")

# QRC is totally a completely different matter. Since they are still providing standard LRC APIs,
# it might be a good idea to leave this mass to the future :)


def _remove_pattern(text: str, pattern: re.Pattern) -> str:
    """Remove all occurrences of pattern from text, then strip leading/trailing whitespace."""
    return pattern.sub("", text).strip()


def _raw_tag_to_cs(mm: str, ss: str, frac: Optional[str]) -> str:
    """Convert parsed time tag components to standard [mm:ss.cc] string."""
    if frac is None:
        ms = 0
    else:
        # cc in [mm:ss:cc] is also treated as centiseconds, per LRC spec
        #             ^
        # why does this format even exist, idk
        n = len(frac)
        if n == 1:
            ms = int(frac) * 100
        elif n == 2:
            ms = int(frac) * 10
        else:
            ms = int(frac)
    cs = min(round(ms / 10), 99)
    return f"[{mm}:{ss}.{cs:02d}]"


def _sanitize_lyric_text(text: str) -> str:
    """Remove possibly word-sync time tags in lyric

    Assumes the normal line-sync time tags are already stripped.
    """
    return _remove_pattern(text, _WORD_SYNC_TAG_RE)


def _reformat(text: str) -> str:
    """Parse each line and reformat to standard [mm:ss.cc]...content form.

    Handles any mix of time tag formats on input. Lines with no time tags
    are stripped of leading/trailing whitespace and passed through unchanged.
    """
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        pos = 0
        tags: list[str] = []
        while True:
            while pos < len(line) and line[pos].isspace():
                pos += 1
            m = _RAW_TAG_RE.match(line, pos)
            # Non-time tags are passed through as-is, except for leading/trailing whitespace which is stripped.
            if not m:
                # No more tags on this line
                break
            tags.append(_raw_tag_to_cs(m.group(1), m.group(2), m.group(3)))
            pos = m.end()
        if tags:
            # This could break lyric lines of some kind of word-synced LRC format, e.g.
            #   [00:01.00]Lyric [00:02.00]line
            # but such format were not planned to be supported in the first place, so…
            out.append(_sanitize_lyric_text("".join(tags) + line[pos:]))
        else:
            out.append(line)
            # Empty lines with no tags are also preserved
    return "\n".join(out)


def _apply_offset(text: str) -> str:
    """Parse [offset:±ms] and shift all standard [mm:ss.cc] tags accordingly.

    Per LRC spec, positive offset = lyrics appear sooner (subtract from timestamps).
    """
    m = _OFFSET_RE.search(text)
    if not m:
        return text
    offset_ms = int(m.group(1))
    text = _OFFSET_RE.sub("", text).strip("\n")
    if offset_ms == 0:
        return text

    def _shift(match: re.Match) -> str:
        total_ms = max(
            0,
            (int(match.group(1)) * 60 + int(match.group(2))) * 1000
            + int(match.group(3)) * 10
            - offset_ms,
        )
        new_mm = total_ms // 60000
        new_ss = (total_ms % 60000) // 1000
        new_cs = min(round((total_ms % 1000) / 10), 99)
        return f"[{new_mm:02d}:{new_ss:02d}.{new_cs:02d}]"

    return _STD_TAG_CAPTURE_RE.sub(_shift, text)


def normalize_tags(text: str) -> str:
    """Normalize LRC to standard form: reformat all tags to [mm:ss.cc], then apply offset."""
    return _apply_offset(_reformat(text))


def is_synced(text: str) -> bool:
    """Check whether text contains non-zero LRC time tags.

    Assumes text has been normalized by normalize (standard [mm:ss.cc] format).
    """
    tags = _STD_TAG_RE.findall(text)
    return bool(tags) and any(tag != "[00:00.00]" for tag in tags)


def detect_sync_status(text: str) -> CacheStatus:
    """Determine whether lyrics contain meaningful LRC time tags.

    Assumes text has been normalized by normalize.
    """
    return (
        CacheStatus.SUCCESS_SYNCED if is_synced(text) else CacheStatus.SUCCESS_UNSYNCED
    )


def normalize_unsynced(lyrics: str) -> str:
    """Normalize unsynced lyrics so every line has a [00:00.00] tag.

    Assumes lyrics have been normalized by normalize.
    - Lines that already have time tags: replace with [00:00.00]
    - Lines without leading tags: prepend [00:00.00]
    - Blank lines in middle are converted to [00:00.00]
    """
    out: list[str] = []
    first = True
    for line in lyrics.splitlines():
        stripped = line.strip()
        if not stripped and not first:
            out.append("[00:00.00]")
            continue
        elif not stripped:
            # Skip leading blank lines
            continue
        first = False
        cleaned = _remove_pattern(line, _LINE_START_STD_TAGS_RE)
        out.append(f"[00:00.00]{cleaned}")
    return "\n".join(out)


def get_audio_path(audio_url: str, ensure_exists: bool = False) -> Optional[Path]:
    """Convert file:// URL to Path, return None if invalid or (if ensure_exists) file doesn't exist."""
    if not audio_url.startswith("file://"):
        return None
    file_path = unquote(audio_url.replace("file://", "", 1))
    path = Path(file_path)
    if ensure_exists and not path.exists():
        return None
    return path


def get_sidecar_path(
    audio_url: str, ensure_audio_exists: bool = False, ensure_exists: bool = False
) -> Optional[Path]:
    """Given a file:// URL, return the corresponding .lrc sidecar path.

    If ensure_audio_exists is True, return None if the audio file does not exist.
    If ensure_exists is True, return None if the .lrc file does not exist.
    """
    audio_path = get_audio_path(audio_url, ensure_exists=ensure_audio_exists)
    if not audio_path:
        return None
    lrc_path = audio_path.with_suffix(".lrc")
    if ensure_exists and not lrc_path.exists():
        return None
    return lrc_path


def to_plain(
    text: str,
    deduplicate: bool = False,
) -> str:
    """Convert lyrics to plain text with all tags stripped.

    If deduplicate is True, only keep the first line of consecutive lines with the same lyric text (after stripping tags).
    Otherwise, lines with multiple time tags will be duplicated as many times as the number of tags.
    Assumes text has been normalized by normalize.
    """

    if not is_synced(text):
        # If there are no meaningful time tags, just strip all tags and return
        return _remove_pattern(text, _LINE_START_TAGS_RE)

    lines = []
    for line in text.splitlines():
        pos = 0
        cnt = 0
        plain_line = ""
        while True:
            # Only match strictly repeated standard time tags at the start of the line
            # Lines without any time tags are ignored.
            # Lyric lines are considered already stripped of whitespaces, so no strips here.
            m = _STD_TAG_RE.match(line, pos)
            if not m:
                plain_line += line[pos:]
                break
            pos = m.end()
            cnt += 1
        # Also avoid dulplicating blank lines
        if deduplicate or not plain_line:
            if cnt > 0:
                lines.append(plain_line)
        else:
            for _ in range(cnt):
                lines.append(plain_line)

    if deduplicate:
        # Remove consecutive duplicates
        deduped_lines = []
        prev_line = None
        for line in lines:
            if line != prev_line:
                deduped_lines.append(line)
            prev_line = line
        lines = deduped_lines

    return "\n".join(lines)


def print_lyrics(
    text: str,
    plain: bool = False,
) -> None:
    """Print lyrics, optionally stripping tags.

    Assumes text has been normalized by normalize.
    """
    if plain:
        print(to_plain(text))
    else:
        print(text)
