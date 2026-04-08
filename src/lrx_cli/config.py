"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 10:17:56
Description: Global configuration constants and logger setup.
"""

import os
import sys
from pathlib import Path
from platformdirs import user_cache_dir, user_config_dir
from dotenv import load_dotenv
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

# .env loading
_config_env = Path(user_config_dir(APP_NAME, APP_AUTHOR)) / ".env"
load_dotenv(_config_env)  # ~/.config/lrx-cli/.env
load_dotenv()  # .env in cwd (does NOT override existing vars)

# HTTP
HTTP_TIMEOUT = 10.0

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

# Player preference (used when multiple MPRIS players are active)
PREFERRED_PLAYER = os.environ.get("PREFERRED_PLAYER", "spotify")


class _Credentials:
    """Credential config with lazy os.environ reads.

    Stable constants live as module-level names above.
    Credentials are @property so monkeypatch.setenv / monkeypatch.delenv
    affect them without needing to patch each consumer separately.
    """

    @property
    def SPOTIFY_SP_DC(self) -> str:
        return os.environ.get("SPOTIFY_SP_DC", "")

    @property
    def QQ_MUSIC_API_URL(self) -> str:
        return os.environ.get("QQ_MUSIC_API_URL", "").rstrip("/")

    @property
    def MUSIXMATCH_USERTOKEN(self) -> str:
        return os.environ.get("MUSIXMATCH_USERTOKEN", "")


credentials = _Credentials()

os.makedirs(CACHE_DIR, exist_ok=True)

# Logger
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
