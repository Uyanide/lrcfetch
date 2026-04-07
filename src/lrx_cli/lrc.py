"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 21:54:01
Description: LRC parsing, modeling, and serialization helpers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import re
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from .models import CacheStatus

# Parses any time tag input format:
#   [mm:ss], [mm:ss.c], [mm:ss.cc], [mm:ss.ccc], [mm:ss:cc], …
_RAW_TAG_RE = re.compile(r"\[(\d{2,}):(\d{2})(?:[.:](\d{1,3}))?\]")

# One or more leading bracket tags at line start.
# Used to strip start tags in plain-mode fallback.
_LINE_START_TAGS_RE = re.compile(r"^(?:\[[^\]]*\])+", re.MULTILINE)

# Timed word-sync tags: <mm:ss>, <mm:ss.c>, <mm:ss.cc>, <mm:ss:cc>
_WORD_SYNC_TAG_RE = re.compile(r"<(\d{2,}):(\d{2})(?:[.:](\d{1,3}))?>")

# A single doc-level tag line: [key:value].
# Disallow nested [] in value so multi-tag lines are not treated as doc tags.
_DOC_TAG_RE = re.compile(r"^\[([^:\]\[]+):([^\[\]]*)\]$")

# QRC uses a different format and is intentionally out of scope here.


def _remove_pattern(text: str, pattern: re.Pattern) -> str:
    """Remove all occurrences of pattern from text, then strip leading/trailing whitespace."""
    return pattern.sub("", text).strip()


def _raw_tag_to_ms(mm: str, ss: str, frac: Optional[str]) -> int:
    """Convert parsed time tag components to total milliseconds."""
    if frac is None:
        ms = 0
    else:
        n = len(frac)
        if n == 1:
            ms = int(frac) * 100
        elif n == 2:
            ms = int(frac) * 10
        else:
            ms = int(frac)
    return (int(mm) * 60 + int(ss)) * 1000 + ms


def _ms_to_std_tag(total_ms: int) -> str:
    mm = max(0, total_ms) // 60000
    ss = (max(0, total_ms) % 60000) // 1000
    cs = min(round((max(0, total_ms) % 1000) / 10), 99)
    return f"[{mm:02d}:{ss:02d}.{cs:02d}]"


def _ms_to_word_tag(total_ms: int) -> str:
    mm = max(0, total_ms) // 60000
    ss = (max(0, total_ms) % 60000) // 1000
    cs = min(round((max(0, total_ms) % 1000) / 10), 99)
    return f"<{mm:02d}:{ss:02d}.{cs:02d}>"


@dataclass(frozen=True)
class LrcWordSegment:
    text: str
    time_ms: Optional[int] = None
    duration_ms: Optional[int] = None


class BaseLine(ABC):
    """Common line interface for rendering and text extraction."""

    @property
    @abstractmethod
    def text(self) -> str:
        """Return plain text content for this line."""

    @abstractmethod
    def to_text(self, include_word_sync: bool) -> str:
        """Return full serialized line text."""

    @abstractmethod
    def to_plain_unsynced(self) -> Optional[str]:
        """Return this line's plain-text contribution in unsynced mode."""

    @abstractmethod
    def timed_plain_entries(self) -> list[tuple[int, str]]:
        """Return (timestamp_ms, text) entries for synced plain-mode output."""

    def has_nonzero_timestamp(self) -> bool:
        return any(ts > 0 for ts, _ in self.timed_plain_entries())


@dataclass
class DocTagLine(BaseLine):
    """Represents a single doc tag line like [ar:Artist]."""

    key: str
    value: str

    @property
    def text(self) -> str:
        return f"[{self.key}:{self.value}]"

    def to_text(self, include_word_sync: bool) -> str:
        return self.text

    def to_plain_unsynced(self) -> Optional[str]:
        return None

    def timed_plain_entries(self) -> list[tuple[int, str]]:
        return []


@dataclass
class LyricLine(BaseLine):
    """Lyric line with optional line-level timestamps."""

    line_times_ms: list[int] = field(default_factory=list)
    words: list[LrcWordSegment] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(seg.text for seg in self.words)

    def to_text(self, include_word_sync: bool) -> str:
        prefix = "".join(_ms_to_std_tag(ms) for ms in self.line_times_ms)
        return prefix + self.text

    def to_plain_unsynced(self) -> Optional[str]:
        return _remove_pattern(self.text, _LINE_START_TAGS_RE)

    def timed_plain_entries(self) -> list[tuple[int, str]]:
        return [(tag_ms, self.text) for tag_ms in self.line_times_ms]


@dataclass
class WordSyncLyricLine(LyricLine):
    """Lyric line that can render per-word sync tags when requested."""

    def to_text(self, include_word_sync: bool) -> str:
        prefix = "".join(_ms_to_std_tag(ms) for ms in self.line_times_ms)
        if not include_word_sync:
            return prefix + self.text
        parts: list[str] = []
        for seg in self.words:
            if seg.time_ms is not None:
                parts.append(_ms_to_word_tag(seg.time_ms))
            parts.append(seg.text)
        return prefix + "".join(parts)


def _split_trimmed_lines(text: str) -> list[str]:
    """Split text into lines, strip each line, and drop outer blank lines."""

    lines = [line.strip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _extract_leading_line_tags(line: str) -> tuple[list[int], str]:
    """Parse leading line-sync tags and return (times_ms, lyric_part).

    Spaces between consecutive leading tags are dropped. If non-space text
    appears, parsing of leading tags stops and the remainder is lyric text.
    """
    pos = 0
    tags_ms: list[int] = []
    while True:
        m = _RAW_TAG_RE.match(line, pos)
        if not m:
            break
        tags_ms.append(_raw_tag_to_ms(m.group(1), m.group(2), m.group(3)))
        pos = m.end()

        # Allow spaces only between consecutive leading tags.
        # We only check for '[' here; the next loop decides whether it is a valid time tag.
        scan = pos
        while scan < len(line) and line[scan].isspace():
            scan += 1
        if scan < len(line) and line[scan] == "[":
            pos = scan
            continue
        pos = scan
        break
    return tags_ms, line[pos:]


def _parse_word_segments(lyric_part: str) -> tuple[list[LrcWordSegment], bool]:
    """Parse timed word-sync tags while preserving all lyric text exactly."""
    segments: list[LrcWordSegment] = []
    cursor = 0
    current_time: Optional[int] = None
    has_word_sync = False

    for m in _WORD_SYNC_TAG_RE.finditer(lyric_part):
        piece = lyric_part[cursor : m.start()]
        if piece:
            segments.append(LrcWordSegment(text=piece, time_ms=current_time))
        current_time = _raw_tag_to_ms(m.group(1), m.group(2), m.group(3))
        has_word_sync = True
        cursor = m.end()

    tail = lyric_part[cursor:]
    if tail or not segments:
        segments.append(
            LrcWordSegment(
                text=tail,
                time_ms=current_time if has_word_sync else None,
            )
        )
    return segments, has_word_sync


def _is_single_doc_tag_line(line: str) -> Optional[tuple[str, str]]:
    """Return (key, value) only for standalone single doc-tag lines."""

    if _RAW_TAG_RE.fullmatch(line):
        return None
    m = _DOC_TAG_RE.fullmatch(line)
    if not m:
        return None
    key = m.group(1).strip()
    value = m.group(2).strip()
    return key, value


class LRCData:
    _lines: list[BaseLine]
    _doc_tags: dict[str, str]

    def __init__(self, text: Optional[str] = None) -> None:
        self._doc_tags = {}
        if not text:
            self._lines = []
            return

        raw_lines = _split_trimmed_lines(text)
        parsed: list[BaseLine] = []

        for raw in raw_lines:
            maybe_tag = _is_single_doc_tag_line(raw)
            if maybe_tag is not None:
                key, value = maybe_tag
                self._doc_tags[key] = value
                parsed.append(DocTagLine(key=key, value=value))
                continue

            tags_ms, lyric_part = _extract_leading_line_tags(raw)
            words, has_word_sync = _parse_word_segments(lyric_part if tags_ms else raw)

            if has_word_sync:
                parsed.append(WordSyncLyricLine(line_times_ms=tags_ms, words=words))
            else:
                parsed.append(LyricLine(line_times_ms=tags_ms, words=words))

        self._lines = parsed

    def __str__(self) -> str:
        return self.to_text(plain=False, include_word_sync=False)

    def __repr__(self) -> str:
        return f"LRCData(doc_tags={self._doc_tags!r}, lines={self._lines!r})"

    def __len__(self) -> int:
        return len(self._lines)

    @property
    def tags(self) -> dict[str, str]:
        return self._doc_tags

    @property
    def lines(self) -> list[BaseLine]:
        return self._lines

    def is_synced(self) -> bool:
        """Return True if any lyric line contains a non-zero line timestamp."""
        return any(line.has_nonzero_timestamp() for line in self._lines)

    def detect_sync_status(self) -> CacheStatus:
        """Map sync detection result to cache status."""
        return (
            CacheStatus.SUCCESS_SYNCED
            if self.is_synced()
            else CacheStatus.SUCCESS_UNSYNCED
        )

    def normalize_unsynced(self) -> "LRCData":
        """Convert lyrics into unsynced LRC form with [00:00.00] tags.

        - Leading blank lyric lines are skipped.
        - Middle blank lyric lines are preserved as empty synced lines.
        - Doc-tag lines are preserved unchanged.
        """
        out: list[BaseLine] = []
        first = True
        for line in self._lines:
            if isinstance(line, DocTagLine):
                out.append(DocTagLine(key=line.key, value=line.value))
                continue

            assert isinstance(line, LyricLine)

            stripped = line.text.strip()
            if not stripped and not first:
                out.append(
                    LyricLine(line_times_ms=[0], words=[LrcWordSegment(text="")])
                )
                continue
            elif not stripped:
                continue
            first = False
            out.append(
                LyricLine(
                    line_times_ms=[0],
                    words=[LrcWordSegment(text=line.text)],
                )
            )
        ret = LRCData()
        ret._lines = out
        ret._doc_tags = dict(self._doc_tags)
        return ret

    def to_plain(
        self,
        deduplicate: bool = False,
    ) -> str:
        """Convert lyrics to plain text with all tags stripped.

        If synced, output is sorted by line timestamp and duplicated for multi-tag lines.
        If not synced, leading bracket tags are stripped per line and original order is kept.
        If deduplicate is True, only consecutive duplicate plain lines are collapsed.
        """

        if not self.is_synced():
            plain_lines = [
                text
                for text in (line.to_plain_unsynced() for line in self._lines)
                if text is not None
            ]
            return "\n".join(plain_lines).strip("\n")

        tagged_lines: list[tuple[int, str]] = []
        for line in self._lines:
            tagged_lines.extend(line.timed_plain_entries())

        sorted_lines = [lyric for _, lyric in sorted(tagged_lines, key=lambda x: x[0])]

        if deduplicate:
            # Remove consecutive duplicates
            deduped_lines = []
            prev_line = None
            for line in sorted_lines:
                if line != prev_line:
                    deduped_lines.append(line)
                prev_line = line
            sorted_lines = deduped_lines

        return "\n".join(sorted_lines).strip()

    def to_unsynced(self) -> "LRCData":
        """Return a plain-text based unsynced representation."""
        return LRCData(self.to_plain())

    def to_text(
        self,
        plain: bool = False,
        include_word_sync: bool = False,
    ) -> str:
        """Serialize to LRC text or plain text.

        - plain=True returns to_plain().
        - include_word_sync controls rendering of per-word tags for word-sync lines.
        """
        if plain:
            return self.to_plain(deduplicate=False)

        lines: list[str] = [
            line.to_text(include_word_sync=include_word_sync) for line in self._lines
        ]
        return "\n".join(lines)


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
    audio_url: str,
    ensure_audio_exists: bool = False,
    ensure_exists: bool = False,
    extension: str = ".lrc",
) -> Optional[Path]:
    """Given a file:// URL, return the corresponding .lrc sidecar path.

    If ensure_audio_exists is True, return None if the audio file does not exist.
    If ensure_exists is True, return None if the .lrc file does not exist.
    """
    audio_path = get_audio_path(audio_url, ensure_exists=ensure_audio_exists)
    if not audio_path:
        return None
    lrc_path = audio_path.with_suffix(extension)
    if ensure_exists and not lrc_path.exists():
        return None
    return lrc_path
