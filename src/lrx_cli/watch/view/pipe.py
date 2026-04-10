"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-04-10 08:15:17
Description: Pipe output implementation for watch mode.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
import sys

from . import BaseOutput, WatchState, WatchStatus


@dataclass(slots=True)
class PipeOutput(BaseOutput):
    """Render a fixed lyric context window to stdout for streaming/pipe usage."""

    before: int = 0
    after: int = 0
    no_newline: bool = False

    def _window_size(self) -> int:
        """Return rendered lyric window size."""
        return self.before + 1 + self.after

    def _render_status(self, message: str) -> list[str]:
        """Render centered status line in fixed-size window."""
        lines = [""] * self._window_size()
        lines[self.before] = message
        return lines

    def _render_lyrics(self, state: WatchState) -> list[str]:
        """Render context lines centered on current timed lyric entry."""
        if state.lyrics is None:
            return self._render_status("[no lyrics]")

        all_lines = state.lyrics.lines
        if not all_lines:
            return self._render_status("[no lyrics]")
        entries = state.lyrics.timed_line_entries

        effective_ms = state.position_ms + state.offset_ms
        current_line_idx: int | None
        if entries and effective_ms < entries[0][0]:
            # playback hasn't reached the first lyric yet; treat current slot as empty
            # so the after-window can show upcoming lines without a "current" anchor
            current_line_idx = None
        else:
            if not entries:
                current_line_idx = 0
            else:
                # bisect_right - 1 gives the last entry whose timestamp <= effective_ms
                current_entry_idx = (
                    bisect_right(state.lyrics.timestamps, effective_ms) - 1
                )
                if current_entry_idx < 0:
                    current_entry_idx = 0
                current_line_idx = entries[current_entry_idx][1]

        out: list[str] = []
        for rel in range(-self.before, self.after + 1):
            if current_line_idx is None:
                # before-first-timestamp: before/current slots are empty; after slots
                # show lines starting from index 0 (rel=1 → line 0, rel=2 → line 1, …)
                if rel <= 0:
                    out.append("")
                    continue
                line_idx = rel - 1
            else:
                line_idx = current_line_idx + rel

            if 0 <= line_idx < len(all_lines):
                out.append(all_lines[line_idx])
            else:
                out.append("")

        return out

    async def on_state(self, state: WatchState) -> None:
        """Render and flush one frame for the latest watch state."""
        if state.status == WatchStatus.FETCHING:
            lines = self._render_status("[fetching...]")
        elif state.status == WatchStatus.NO_LYRICS:
            lines = self._render_status("[no lyrics]")
        elif state.status == WatchStatus.IDLE:
            lines = self._render_status("[idle]")
        else:
            lines = self._render_lyrics(state)

        for line in lines:
            # no_newline mode lets callers use \r to overwrite the previous frame in-place
            sys.stdout.write(line + ("\n" if not self.no_newline else ""))
        sys.stdout.flush()
