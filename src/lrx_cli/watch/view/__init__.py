"""Output abstraction types for watch mode rendering."""

from abc import ABC, abstractmethod
from bisect import bisect_right
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ...lrc import LRCData, LyricLine
from ...models import TrackMeta


class WatchStatus(str, Enum):
    IDLE = "idle"
    FETCHING = "fetching"
    OK = "ok"
    NO_LYRICS = "no_lyrics"


@dataclass(slots=True, frozen=True)
class LyricView:
    """View-ready immutable lyric data projected from one normalized LRC object."""

    normalized: LRCData
    lines: tuple[str, ...]
    timed_line_entries: tuple[tuple[int, int], ...]
    timestamps: tuple[int, ...]

    @staticmethod
    def from_lrc(lyrics: LRCData) -> "LyricView":
        """Build a view projection once from normalized lyrics."""
        normalized = lyrics.normalize()

        lines: list[str] = []
        entries: list[tuple[int, int]] = []

        line_index = 0
        for line in normalized.lines:
            if not isinstance(line, LyricLine):
                continue
            text = line.text
            lines.append(text)
            timestamp = line.line_times_ms[0] if line.line_times_ms else 0
            entries.append((max(0, timestamp), line_index))
            line_index += 1

        timestamps = tuple(timestamp for timestamp, _ in entries)
        return LyricView(
            normalized=normalized,
            lines=tuple(lines),
            timed_line_entries=tuple(entries),
            timestamps=timestamps,
        )

    def signature_cursor(self, at_ms: int) -> tuple:
        """Build a stable cursor signature for dedupe decisions."""
        if not self.timed_line_entries:
            return ("plain", self.lines)

        first_ts = self.timed_line_entries[0][0]
        if at_ms < first_ts:
            return ("before_first", first_ts)

        idx = bisect_right(self.timestamps, at_ms) - 1
        if idx < 0:
            idx = 0

        ts, line_idx = self.timed_line_entries[idx]
        text = self.lines[line_idx] if line_idx < len(self.lines) else ""
        return ("ok", idx, ts, text)


@dataclass(slots=True)
class WatchState:
    """Immutable snapshot payload delivered from session to output implementations."""

    track: Optional[TrackMeta]
    lyrics: Optional[LyricView]
    position_ms: int
    offset_ms: int
    status: WatchStatus


class BaseOutput(ABC):
    @abstractmethod
    async def on_state(self, state: WatchState) -> None:
        """Render or deliver one watch state frame."""
        ...
