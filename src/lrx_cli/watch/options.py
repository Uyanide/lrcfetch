"""Watch runtime options passed from CLI composition root."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WatchOptions:
    """Runtime settings used by watch components."""

    preferred_player: str
    player_blacklist: tuple[str, ...]
    debounce_ms: int
    position_tick_ms: int
    calibration_interval_s: float
    socket_path: Path
