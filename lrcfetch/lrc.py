"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 21:54:01
Description: Shared LRC time-tag utilities
"""

import re

from .models import CacheStatus

# Standard format: [mm:ss.cc] or [mm:ss.ccc]
_STANDARD_TAG_RE = re.compile(r"\[\d{2}:\d{2}\.\d{2,3}\]")

# Non-standard format: [mm:ss:cc] (two colons instead of dot)
_COLON_TAG_RE = re.compile(r"\[(\d{2}:\d{2}):(\d{2,3})\]")

# Matches any LRC time tag (standard or non-standard) at start of line
LRC_LINE_RE = re.compile(r"^\[(\d{2}:\d{2}[.:]\d{2,3})\]", re.MULTILINE)

# All-zero tags
_ZERO_TAG_RE = re.compile(r"^\[00:00[.:]0{2,3}\]$")

# [offset:+/-xxx] tag — value in milliseconds
_OFFSET_RE = re.compile(r"^\[offset:\s*([+-]?\d+)\]\s*$", re.MULTILINE | re.IGNORECASE)

# Time tag for offset application: captures mm, ss, cc/ccc
_TIME_TAG_RE = re.compile(r"\[(\d{2}):(\d{2})\.(\d{2,3})\]")


def _apply_offset(text: str) -> str:
    """Parse [offset:±ms] tag and shift all time tags accordingly.

    Per LRC spec, a positive offset means lyrics appear sooner (subtract
    from timestamps), negative means later (add to timestamps).
    """
    m = _OFFSET_RE.search(text)
    if not m:
        return text
    offset_ms = int(m.group(1))
    if offset_ms == 0:
        return _OFFSET_RE.sub("", text).strip("\n")

    # Remove the offset tag line
    text = _OFFSET_RE.sub("", text)

    def _shift(match: re.Match) -> str:
        mm, ss, cs = int(match.group(1)), int(match.group(2)), match.group(3)
        # Normalize centiseconds to milliseconds
        if len(cs) == 2:
            ms = int(cs) * 10
            fmt_cs = 2
        else:
            ms = int(cs)
            fmt_cs = 3
        total_ms = (mm * 60 + ss) * 1000 + ms - offset_ms
        total_ms = max(0, total_ms)
        new_mm = total_ms // 60000
        new_ss = (total_ms % 60000) // 1000
        new_cs = total_ms % 1000
        if fmt_cs == 2:
            new_cs = new_cs // 10
            return f"[{new_mm:02d}:{new_ss:02d}.{new_cs:02d}]"
        return f"[{new_mm:02d}:{new_ss:02d}.{new_cs:03d}]"

    return _TIME_TAG_RE.sub(_shift, text)


def normalize_tags(text: str) -> str:
    """Normalize LRC time tags: colon format → dot format, then apply offset."""
    text = _COLON_TAG_RE.sub(r"[\1.\2]", text)
    return _apply_offset(text)


def is_synced(text: str) -> bool:
    """Check whether text contains actual LRC time tags with non-zero times.

    Returns False if no tags exist or all tags are [00:00.00].
    Handles both [mm:ss.cc] and [mm:ss:cc] formats.
    """
    tags = _STANDARD_TAG_RE.findall(text)
    # Also check non-standard format
    tags += [f"[{m.group(1)}.{m.group(2)}]" for m in _COLON_TAG_RE.finditer(text)]
    if not tags:
        return False
    for tag in tags:
        if not _ZERO_TAG_RE.match(tag):
            return True
    return False


def detect_sync_status(text: str) -> CacheStatus:
    """Determine whether lyrics contain meaningful LRC time tags."""
    return (
        CacheStatus.SUCCESS_SYNCED if is_synced(text) else CacheStatus.SUCCESS_UNSYNCED
    )
