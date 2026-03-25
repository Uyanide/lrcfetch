"""Spotify fetcher — obtains synced lyrics via Spotify's internal color-lyrics API.

Authentication flow (mirrors spotify-lyrics Go implementation):
  1. Fetch server time from Spotify
  2. Fetch TOTP secret from xyloflake/spot-secrets-go
  3. Generate a TOTP code and exchange it (with SP_DC cookie) for an access token
  4. Request lyrics using the access token

The secret and token are cached on the instance to avoid redundant network
calls within the same session.

Requires SPOTIFY_SP_DC environment variable to be set.
"""

import httpx
import time
import struct
import hmac
import hashlib
from typing import Optional, Tuple
from loguru import logger

from lrcfetch.models import TrackMeta, LyricResult, CacheStatus
from lrcfetch.fetchers.base import BaseFetcher
from lrcfetch.config import (
    HTTP_TIMEOUT,
    TTL_NOT_FOUND,
    TTL_NETWORK_ERROR,
    SPOTIFY_TOKEN_URL,
    SPOTIFY_LYRICS_URL,
    SPOTIFY_SERVER_TIME_URL,
    SPOTIFY_SECRET_URL,
    SPOTIFY_SP_DC,
    UA_BROWSER,
)


class SpotifyFetcher(BaseFetcher):
    def __init__(self) -> None:
        # Session-level caches to avoid refetching within the same run
        self._cached_secret: Optional[Tuple[str, int]] = None
        self._cached_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    @property
    def source_name(self) -> str:
        return "spotify"

    # ─── Auth helpers ────────────────────────────────────────────────

    def _get_server_time(self, client: httpx.Client) -> Optional[int]:
        """Fetch Spotify's server timestamp (seconds since epoch)."""
        try:
            res = client.get(SPOTIFY_SERVER_TIME_URL, timeout=HTTP_TIMEOUT)
            res.raise_for_status()
            data = res.json()
            if not isinstance(data, dict) or "serverTime" not in data:
                logger.error(f"Spotify: unexpected server-time response: {data}")
                return None
            server_time = data["serverTime"]
            logger.debug(f"Spotify: server time = {server_time}")
            return server_time
        except Exception as e:
            logger.error(f"Spotify: failed to fetch server time: {e}")
            return None

    def _get_secret(self, client: httpx.Client) -> Optional[Tuple[str, int]]:
        """Fetch and decode the TOTP secret. Cached after first success.

        Response format: [{version: int, secret: str}, ...]
        Each character in *secret* is XOR-decoded with ``(index % 33) + 9``.
        """
        if self._cached_secret is not None:
            logger.debug("Spotify: using cached TOTP secret")
            return self._cached_secret

        try:
            res = client.get(SPOTIFY_SECRET_URL, timeout=HTTP_TIMEOUT)
            res.raise_for_status()
            data = res.json()

            if not isinstance(data, list) or len(data) == 0:
                logger.error(
                    f"Spotify: unexpected secrets response (type={type(data).__name__}, len={len(data) if isinstance(data, list) else '?'})")
                return None

            last = data[-1]
            if "secret" not in last or "version" not in last:
                logger.error(f"Spotify: malformed secret entry: {list(last.keys())}")
                return None

            secret_raw = last["secret"]
            version = last["version"]

            # XOR decode
            parts = []
            for i, char in enumerate(secret_raw):
                parts.append(str(ord(char) ^ ((i % 33) + 9)))
            secret = "".join(parts)

            logger.debug(f"Spotify: decoded secret v{version} (len={len(secret)})")
            self._cached_secret = (secret, version)
            return self._cached_secret

        except Exception as e:
            logger.error(f"Spotify: failed to fetch secret: {e}")
            return None

    @staticmethod
    def _generate_totp(server_time_s: int, secret: str) -> str:
        """Generate a 6-digit TOTP code compatible with Spotify's auth.

        Uses HMAC-SHA1 with a 30-second period, matching the Go reference.
        """
        counter = server_time_s // 30
        counter_bytes = struct.pack(">Q", counter)

        mac = hmac.new(secret.encode(), counter_bytes, hashlib.sha1).digest()

        offset = mac[-1] & 0x0F
        binary_code = (
            (mac[offset] & 0x7F) << 24
            | (mac[offset + 1] & 0xFF) << 16
            | (mac[offset + 2] & 0xFF) << 8
            | (mac[offset + 3] & 0xFF)
        )

        code = binary_code % (10**6)
        return str(code).zfill(6)

    def _get_token(self) -> Optional[str]:
        """Obtain a Spotify access token. Cached until expiry.

        Requires SP_DC cookie (set via SPOTIFY_SP_DC env var).
        """
        # Return cached token if still valid (with 30s safety margin)
        if self._cached_token and time.time() < self._token_expires_at - 30:
            logger.debug("Spotify: using cached access token")
            return self._cached_token

        if not SPOTIFY_SP_DC:
            logger.error(
                "Spotify: SPOTIFY_SP_DC env var not set — "
                "cannot authenticate with Spotify"
            )
            return None

        headers = {
            "User-Agent": UA_BROWSER,
            "Cookie": f"sp_dc={SPOTIFY_SP_DC}",
        }

        with httpx.Client(headers=headers) as client:
            # Step 1: server time
            server_time = self._get_server_time(client)
            if server_time is None:
                return None

            # Step 2: secret
            secret_data = self._get_secret(client)
            if secret_data is None:
                return None

            secret, version = secret_data

            # Step 3: TOTP
            totp = self._generate_totp(server_time, secret)
            logger.debug(f"Spotify: generated TOTP v{version}: {totp}")

            # Step 4: exchange for token
            params = {
                "reason": "transport",
                "productType": "web-player",
                "totp": totp,
                "totpVer": str(version),
                "ts": str(int(time.time())),
            }

            try:
                res = client.get(SPOTIFY_TOKEN_URL, params=params, timeout=HTTP_TIMEOUT)
                if res.status_code != 200:
                    logger.error(
                        f"Spotify: token request returned {res.status_code}"
                    )
                    return None

                body = res.json()

                if not isinstance(body, dict) or "accessToken" not in body:
                    logger.error(
                        f"Spotify: unexpected token response keys: {list(body.keys()) if isinstance(body, dict) else type(body).__name__}")
                    return None

                token = body["accessToken"]
                is_anonymous = body.get("isAnonymous", False)
                if is_anonymous:
                    logger.warning(
                        "Spotify: received anonymous token — SP_DC may be invalid"
                    )

                # Cache with reported expiry
                expires_ms = body.get("accessTokenExpirationTimestampMs", 0)
                if expires_ms and expires_ms > int(time.time() * 1000):
                    self._token_expires_at = expires_ms / 1000.0
                else:
                    logger.warning("Spotify: token expiry missing or invalid")
                    self._token_expires_at = time.time() + 3600

                self._cached_token = token
                logger.debug("Spotify: obtained access token")
                return token

            except Exception as e:
                logger.error(f"Spotify: token request failed: {e}")
                return None

    # ─── Lyrics ──────────────────────────────────────────────────────

    @staticmethod
    def _format_lrc_line(start_ms: int, words: str) -> str:
        """Format a single lyric line as LRC ``[mm:ss.cc]text``."""
        minutes = start_ms // 60000
        seconds = (start_ms // 1000) % 60
        centiseconds = round((start_ms % 1000) / 10.0)
        return f"[{minutes:02d}:{seconds:02d}.{centiseconds:02.0f}]{words}"

    @staticmethod
    def _is_truly_synced(lines: list[dict]) -> bool:
        """Check if lyrics are actually synced (not all timestamps zero)."""
        for line in lines:
            try:
                ms = int(line.get("startTimeMs", "0"))
                if ms > 0:
                    return True
            except (ValueError, TypeError):
                continue
        return False

    def fetch(self, track: TrackMeta) -> Optional[LyricResult]:
        """Fetch lyrics for a Spotify track by its track ID."""
        if not track.trackid:
            logger.debug("Spotify: skipped — no trackid in metadata")
            return None

        logger.info(f"Spotify: fetching lyrics for trackid={track.trackid}")

        token = self._get_token()
        if not token:
            logger.error("Spotify: cannot fetch lyrics without a token")
            return LyricResult(status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR)

        url = f"{SPOTIFY_LYRICS_URL}{track.trackid}?format=json&market=from_token"
        headers = {
            "User-Agent": UA_BROWSER,
            "Authorization": f"Bearer {token}",
            "App-Platform": "WebPlayer",
        }

        try:
            with httpx.Client(timeout=HTTP_TIMEOUT) as client:
                res = client.get(url, headers=headers)

                if res.status_code == 404:
                    logger.debug(f"Spotify: 404 for trackid={track.trackid}")
                    return LyricResult(
                        status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND
                    )

                if res.status_code != 200:
                    logger.error(f"Spotify: lyrics API returned {res.status_code}")
                    return LyricResult(
                        status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR
                    )

                data = res.json()

            # Validate response structure
            if not isinstance(data, dict) or "lyrics" not in data:
                logger.error(f"Spotify: unexpected lyrics response structure")
                return LyricResult(
                    status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR
                )

            lyrics_data = data["lyrics"]
            sync_type = lyrics_data.get("syncType", "")
            lines = lyrics_data.get("lines", [])

            if not isinstance(lines, list) or len(lines) == 0:
                logger.debug("Spotify: response contained no lyric lines")
                return LyricResult(status=CacheStatus.NOT_FOUND, ttl=TTL_NOT_FOUND)

            # Determine sync status
            # syncType == "LINE_SYNCED" AND at least one non-zero timestamp
            is_synced = sync_type == "LINE_SYNCED" and self._is_truly_synced(lines)

            # Convert to LRC
            lrc_lines: list[str] = []
            for line in lines:
                words = line.get("words", "")
                if not isinstance(words, str):
                    continue
                try:
                    ms = int(line.get("startTimeMs", "0"))
                except (ValueError, TypeError):
                    ms = 0

                if is_synced:
                    lrc_lines.append(self._format_lrc_line(ms, words))
                else:
                    # Unsynced: emit with zero timestamps
                    lrc_lines.append(f"[00:00.00]{words}")

            content = "\n".join(lrc_lines)
            status = CacheStatus.SUCCESS_SYNCED if is_synced else CacheStatus.SUCCESS_UNSYNCED

            logger.info(
                f"Spotify: got {status.value} lyrics ({len(lrc_lines)} lines)"
            )
            return LyricResult(status=status, lyrics=content, source=self.source_name)

        except Exception as e:
            logger.error(f"Spotify: lyrics fetch failed: {e}")
            return LyricResult(status=CacheStatus.NETWORK_ERROR, ttl=TTL_NETWORK_ERROR)
