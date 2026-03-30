"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 10:17:56
Description: Global configuration constants and logger setup
"""

import os
import sys
from pathlib import Path
from platformdirs import user_cache_dir, user_config_dir
from dotenv import load_dotenv
from loguru import logger

# Application
APP_NAME = "lrcfetch"
APP_AUTHOR = "Uyanide"

# Paths
CACHE_DIR = user_cache_dir(APP_NAME, APP_AUTHOR)
DB_PATH = os.path.join(CACHE_DIR, "cache.db")

# .env loading
_config_env = Path(user_config_dir(APP_NAME, APP_AUTHOR)) / ".env"
load_dotenv(_config_env)  # ~/.config/lrcfetch/.env
load_dotenv()  # .env in cwd (does NOT override existing vars)

# HTTP
HTTP_TIMEOUT = 10.0

# Cache TTLs (seconds)
TTL_SYNCED = None  # never expires
TTL_UNSYNCED = 86400  # 1 day
TTL_NOT_FOUND = 86400 * 3  # 3 days
TTL_NETWORK_ERROR = 3600  # 1 hour

# Search
DURATION_TOLERANCE_MS = 3000  # max duration mismatch for search matching

# Spotify related
SPOTIFY_TOKEN_URL = "https://open.spotify.com/api/token"
SPOTIFY_LYRICS_URL = "https://spclient.wg.spotify.com/color-lyrics/v2/track/"
SPOTIFY_SERVER_TIME_URL = "https://open.spotify.com/api/server-time"
SPOTIFY_SECRET_URL = (
    "https://raw.githubusercontent.com/xyloflake/spot-secrets-go"
    "/refs/heads/main/secrets/secrets.json"
)
SPOTIFY_SP_DC = os.environ.get("SPOTIFY_SP_DC", "")
SPOTIFY_TOKEN_CACHE_FILE = os.path.join(CACHE_DIR, "spotify_token.json")

# Netease api
NETEASE_SEARCH_URL = "https://music.163.com/api/cloudsearch/pc"
NETEASE_LYRIC_URL = "https://interface3.music.163.com/api/song/lyric"

# LRCLIB api
LRCLIB_API_URL = "https://lrclib.net/api/get"
LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"

# QQ Music API (self-hosted proxy)
QQ_MUSIC_API_URL = os.environ.get("QQ_MUSIC_API_URL", "").rstrip("/")

# User-Agents
UA_BROWSER = "Mozilla/5.0 (X11; Linux x86_64; rv:148.0) Gecko/20100101 Firefox/148.0"
UA_LRCFETCH = "LRCFetch (https://github.com/Uyanide/lrcfetch)"

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
