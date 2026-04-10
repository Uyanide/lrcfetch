"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 10:17:56
Description: Global configuration constants, typed config dataclasses, and logger setup.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_type_hints

from platformdirs import user_cache_dir, user_config_dir
from loguru import logger
from importlib.metadata import version

# Application
APP_NAME = "lrx-cli"
APP_AUTHOR = "Uyanide"
APP_VERSION = version(APP_NAME)

# Paths
CACHE_DIR = user_cache_dir(APP_NAME, APP_AUTHOR)
DB_PATH = os.path.join(CACHE_DIR, "cache.db")
# Slot identifiers used by per-slot cache rows.
SLOT_SYNCED = "SYNCED"
SLOT_UNSYNCED = "UNSYNCED"

_WATCH_SOCKET_PATH = str(Path(CACHE_DIR) / "watch.sock")

# Cache TTLs (seconds)
TTL_SYNCED = None  # never expires
TTL_UNSYNCED = None  # never expires
TTL_NOT_FOUND = 86400 * 3  # 3 days
TTL_NETWORK_ERROR = 3600  # 1 hour

# Search
DURATION_TOLERANCE_MS = 3000  # max duration mismatch for search matching

# Confidence scoring weights (sum to 100)
SCORE_W_TITLE = 40.0
SCORE_W_ARTIST = 30.0
SCORE_W_ALBUM = 10.0
SCORE_W_DURATION = 10.0
SCORE_W_SYNCED = 10.0
CONFIDENCE_ALGO_VERSION = 1

# Confidence thresholds
MIN_CONFIDENCE = 40.0  # below this, candidate is rejected
HIGH_CONFIDENCE = 80.0  # at or above this, stop searching early

# Multi-candidate fetching
MULTI_CANDIDATE_LIMIT = 3  # max candidates to try per search-based fetcher
MULTI_CANDIDATE_DELAY_S = 0.2  # delay between sequential lyric fetches

# Legacy cache rows (no confidence stored) get a base score by sync status
LEGACY_CONFIDENCE = 50.0

# User-Agents
UA_BROWSER = "Mozilla/5.0 (X11; Linux x86_64; rv:149.0) Gecko/20100101 Firefox/149.0"
UA_LRX = f"LRX-CLI {APP_VERSION} (https://github.com/Uyanide/lrx-cli)"

MUSIXMATCH_COOLDOWN_MS = 600_000  # 10 minutes

os.makedirs(CACHE_DIR, exist_ok=True)


DEFAULT_PREFERRED_PLAYER = ""
DEFAULT_PLAYER_BLACKLIST: tuple[str, ...] = (
    "firefox",
    "zen",
    "chrome",
    "chromium",
    "vivaldi",
    "edge",
    "opera",
    "mpv",
)


@dataclass(frozen=True)
class GeneralConfig:
    preferred_player: str = DEFAULT_PREFERRED_PLAYER
    player_blacklist: tuple[str, ...] = DEFAULT_PLAYER_BLACKLIST
    http_timeout: float = 10.0


@dataclass(frozen=True)
class CredentialConfig:
    spotify_sp_dc: str = ""
    musixmatch_usertoken: str = ""
    qq_music_api_url: str = ""


@dataclass(frozen=True)
class WatchConfig:
    debounce_ms: int = 400
    calibration_interval_s: float = 3.0
    position_tick_ms: int = 50
    socket_path: str = field(default_factory=lambda: _WATCH_SOCKET_PATH)


@dataclass(frozen=True)
class AppConfig:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    credentials: CredentialConfig = field(default_factory=CredentialConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)


_CONFIG_PATH = Path(user_config_dir(APP_NAME, APP_AUTHOR)) / "config.toml"


def _coerce(val: Any, hint: Any, section: str, name: str) -> Any:
    """Coerce and validate one TOML value against its declared field type."""
    if hint is str:
        if not isinstance(val, str):
            raise ValueError(
                f"[{section}].{name}: expected str, got {type(val).__name__}"
            )
        return val
    if hint is int:
        if not isinstance(val, int) or isinstance(val, bool):
            raise ValueError(
                f"[{section}].{name}: expected int, got {type(val).__name__}"
            )
        return val
    if hint is float:
        if isinstance(val, bool):
            raise ValueError(f"[{section}].{name}: expected float, got bool")
        if isinstance(val, (int, float)):
            return float(val)
        raise ValueError(
            f"[{section}].{name}: expected float, got {type(val).__name__}"
        )
    origin = getattr(hint, "__origin__", None)
    if origin is tuple:
        if not isinstance(val, list):
            raise ValueError(
                f"[{section}].{name}: expected array, got {type(val).__name__}"
            )
        for i, item in enumerate(val):
            if not isinstance(item, str):
                raise ValueError(
                    f"[{section}].{name}[{i}]: expected str, got {type(item).__name__}"
                )
        return tuple(val)
    raise ValueError(f"[{section}].{name}: unsupported field type {hint!r}")


def _parse_section(raw: dict[str, Any], cls: type, section: str) -> Any:
    """Parse one TOML section dict into a frozen dataclass, rejecting unknown keys."""
    fields_map = {f.name: f for f in dataclasses.fields(cls)}
    hints = get_type_hints(cls)

    unknown = set(raw) - set(fields_map)
    if unknown:
        raise ValueError(
            f"Unknown config keys in [{section}]: {', '.join(sorted(unknown))}"
        )

    kwargs: dict[str, Any] = {}
    for name, f in fields_map.items():
        if name not in raw:
            if f.default is not dataclasses.MISSING:
                kwargs[name] = f.default
            elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
                kwargs[name] = f.default_factory()
            continue
        kwargs[name] = _coerce(raw[name], hints[name], section, name)

    return cls(**kwargs)


def load_config(path: Path | None = None) -> AppConfig:
    """Load AppConfig from TOML file; return all-defaults when file is absent."""
    resolved = path or _CONFIG_PATH
    if not resolved.exists():
        return AppConfig()
    with open(resolved, "rb") as f:
        data = tomllib.load(f)
    return AppConfig(
        general=_parse_section(data.get("general", {}), GeneralConfig, "general"),
        credentials=_parse_section(
            data.get("credentials", {}), CredentialConfig, "credentials"
        ),
        watch=_parse_section(data.get("watch", {}), WatchConfig, "watch"),
    )


_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

logger.remove()
logger.add(sys.stderr, format=_LOG_FORMAT, level="INFO")


def enable_debug() -> None:
    """Switch logger to DEBUG level."""
    logger.remove()
    logger.add(sys.stderr, format=_LOG_FORMAT, level="DEBUG")
