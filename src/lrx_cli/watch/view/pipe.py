"""Pipe output implementation for watch mode."""

from bisect import bisect_right
from dataclasses import dataclass
import sys

from . import BaseOutput, WatchState


@dataclass(slots=True)
class PipeOutput(BaseOutput):
    """Render a fixed lyric context window to stdout for streaming/pipe usage."""

    before: int = 0
    after: int = 0

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
            # Before first timestamp, current lyric is empty and after-window shows upcoming lines.
            current_line_idx = None
        else:
            if not entries:
                current_line_idx = 0
            else:
                current_entry_idx = (
                    bisect_right(state.lyrics.timestamps, effective_ms) - 1
                )
                if current_entry_idx < 0:
                    current_entry_idx = 0
                current_line_idx = entries[current_entry_idx][1]

        out: list[str] = []
        for rel in range(-self.before, self.after + 1):
            if current_line_idx is None:
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
        if state.status == "fetching":
            lines = self._render_status("[fetching...]")
        elif state.status == "no_lyrics":
            lines = self._render_status("[no lyrics]")
        elif state.status == "paused":
            lines = self._render_status("[paused]")
        elif state.status == "idle":
            lines = self._render_status("[idle]")
        else:
            lines = self._render_lyrics(state)

        for line in lines:
            print(line)
        sys.stdout.flush()
