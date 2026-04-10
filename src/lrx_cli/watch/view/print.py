"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-04-10 08:15:31
Description: Print output implementation for watch mode — one shot per track.
"""

from __future__ import annotations

import sys

from . import BaseOutput, WatchState, WatchStatus


class PrintOutput(BaseOutput):
    """Emit full lyrics to stdout once per track transition, then stay silent.

    Deduplication is delegated to the coordinator via position_sensitive=False:
    the coordinator uses a fixed position for signatures, so on_state fires at
    most once per (status, track_key) transition rather than on every tick.
    """

    # fixed position=0 in signatures → coordinator calls on_state only on
    # track/status transitions, never on lyric cursor advances
    position_sensitive = False

    plain: bool

    def __init__(self, plain: bool = False) -> None:
        self.plain = plain

    async def on_state(self, state: WatchState) -> None:
        if state.status == WatchStatus.FETCHING or state.status == WatchStatus.IDLE:
            return

        if state.status == WatchStatus.NO_LYRICS:
            # emit a blank line as a machine-readable sentinel for "track changed, no lyrics"
            sys.stdout.write("\n")
            sys.stdout.flush()
        elif state.status == WatchStatus.OK and state.lyrics is not None:
            lrc = state.lyrics.normalized
            if self.plain:
                text = lrc.to_plain()
            else:
                text = str(lrc)
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
