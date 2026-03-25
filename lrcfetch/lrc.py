"""Shared LRC time-tag utilities.

Handles detection, normalization, and sync-status checks for LRC lyrics.
"""

import re
from lrcfetch.models import CacheStatus

# Standard format: [mm:ss.cc] or [mm:ss.ccc]
_STANDARD_TAG_RE = re.compile(r"\[\d{2}:\d{2}\.\d{2,3}\]")

# Non-standard format: [mm:ss:cc] (two colons instead of dot)
_COLON_TAG_RE = re.compile(r"\[(\d{2}:\d{2}):(\d{2,3})\]")

# Matches any LRC time tag (standard or non-standard) at start of line
LRC_LINE_RE = re.compile(r"^\[(\d{2}:\d{2}[.:]\d{2,3})\]", re.MULTILINE)

# All-zero tags
_ZERO_TAG_RE = re.compile(r"^\[00:00[.:]0{2,3}\]$")


def normalize_tags(text: str) -> str:
    """Convert non-standard time tags [mm:ss:cc] to standard [mm:ss.cc]."""
    return _COLON_TAG_RE.sub(r"[\1.\2]", text)


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
    return CacheStatus.SUCCESS_SYNCED if is_synced(text) else CacheStatus.SUCCESS_UNSYNCED
