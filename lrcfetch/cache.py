"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 10:18:03
Description: SQLite-based lyric cache with per-source storage and TTL expiration
"""

import sqlite3
import hashlib
import time
from typing import Optional
from loguru import logger

from .config import DB_PATH
from .models import TrackMeta, LyricResult, CacheStatus


def _generate_key(track: TrackMeta, source: str) -> str:
    """Generate a unique cache key from track metadata and source.

    The key is scoped by source so that different fetchers can cache
    independently for the same track (e.g. Spotify synced vs Netease unsynced).
    """
    # Spotify tracks always use their track ID as the primary identifier
    if track.trackid and source == "spotify":
        return f"spotify:{track.trackid}"

    parts = []
    if track.artist:
        parts.append(track.artist)
    if track.title:
        parts.append(track.title)
    if track.album:
        parts.append(track.album)
    if track.length:
        parts.append(str(track.length))

    # Fall back to URL for local files
    if not parts and track.url:
        return f"{source}:url:{track.url}"

    if not parts:
        raise ValueError("Insufficient metadata to generate cache key")

    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return f"{source}:{digest}"


class CacheEngine:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Create or migrate the cache table."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    lyrics TEXT,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER,
                    artist TEXT,
                    title TEXT,
                    album TEXT
                )
            """)
            conn.commit()

    # Read

    def get(self, track: TrackMeta, source: str) -> Optional[LyricResult]:
        """Look up a cached result for *track* from *source*.

        Returns None on cache miss or expiration.
        """
        try:
            key = _generate_key(track, source)
        except ValueError:
            return None

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT status, lyrics, source, expires_at FROM cache WHERE key = ?",
                (key,),
            ).fetchone()

            if not row:
                logger.debug(f"Cache miss: {source} / {track.display_name()}")
                return None

            status_str, lyrics, src, expires_at = row

            # Check TTL expiration
            if expires_at and expires_at < int(time.time()):
                logger.debug(f"Cache expired: {source} / {track.display_name()}")
                conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                conn.commit()
                return None

            remaining = expires_at - int(time.time()) if expires_at else None
            logger.debug(
                f"Cache hit: {source} / {track.display_name()} "
                f"[{status_str}, ttl={remaining}s]"
            )
            return LyricResult(
                status=CacheStatus(status_str),
                lyrics=lyrics,
                source=src,
                ttl=remaining,
            )

    def get_best(self, track: TrackMeta, sources: list[str]) -> Optional[LyricResult]:
        """Return the best cached result across *sources* (synced > unsynced).

        Skips negative statuses (NOT_FOUND, NETWORK_ERROR) — those are only
        consulted per-source to avoid redundant fetches.
        """
        best: Optional[LyricResult] = None
        for src in sources:
            cached = self.get(track, src)
            if not cached:
                continue
            if cached.status == CacheStatus.SUCCESS_SYNCED:
                return cached  # Can't do better
            if cached.status == CacheStatus.SUCCESS_UNSYNCED and best is None:
                best = cached
        return best

    # Write

    def set(
        self,
        track: TrackMeta,
        source: str,
        result: LyricResult,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Store a lyric result in the cache."""
        try:
            key = _generate_key(track, source)
        except ValueError:
            logger.warning("Cannot cache: insufficient track metadata.")
            return

        now = int(time.time())
        expires_at = now + ttl_seconds if ttl_seconds else None

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO cache
                   (key, source, status, lyrics, created_at, expires_at,
                    artist, title, album)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    key,
                    source,
                    result.status.value,
                    result.lyrics,
                    now,
                    expires_at,
                    track.artist,
                    track.title,
                    track.album,
                ),
            )
            conn.commit()
        logger.debug(
            f"Cached: {source} / {track.display_name()} "
            f"[{result.status.value}, ttl={ttl_seconds}s]"
        )

    # Delete

    def clear_all(self) -> None:
        """Remove every entry from the cache."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cache")
            conn.commit()
        logger.info("Cache cleared.")

    def clear_track(self, track: TrackMeta) -> None:
        """Remove all cached entries (every source) for a single track."""
        conditions, params = self._track_where(track)
        if not conditions:
            logger.info(f"No cache entries found for {track.display_name()}.")
            return
        where = " AND ".join(conditions)
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(f"DELETE FROM cache WHERE {where}", params)
            conn.commit()
        if cur.rowcount:
            logger.info(
                f"Cleared {cur.rowcount} cache entries for {track.display_name()}."
            )
        else:
            logger.info(f"No cache entries found for {track.display_name()}.")

    def prune(self) -> int:
        """Remove all expired entries. Returns the number of rows deleted."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "DELETE FROM cache WHERE expires_at IS NOT NULL AND expires_at < ?",
                (int(time.time()),),
            )
            conn.commit()
            count = cur.rowcount
        logger.info(f"Pruned {count} expired cache entries.")
        return count

    @staticmethod
    def _track_where(track: TrackMeta) -> tuple[list[str], list[str]]:
        """Build WHERE conditions to match a track across all sources."""
        conditions: list[str] = []
        params: list[str] = []
        if track.artist:
            conditions.append("artist = ?")
            params.append(track.artist)
        if track.title:
            conditions.append("title = ?")
            params.append(track.title)
        if track.album:
            conditions.append("album = ?")
            params.append(track.album)
        return conditions, params

    # Query / inspect

    def query_track(self, track: TrackMeta) -> list[dict]:
        """Return all cached rows for a given track (across all sources)."""
        conditions, params = self._track_where(track)
        if not conditions:
            return []
        where = " AND ".join(conditions)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [
                dict(r)
                for r in conn.execute(
                    f"SELECT * FROM cache WHERE {where}", params
                ).fetchall()
            ]

    def query_all(self) -> list[dict]:
        """Return every row in the cache table."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM cache").fetchall()]

    def stats(self) -> dict:
        """Return aggregate cache statistics."""
        now = int(time.time())
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            expired = conn.execute(
                "SELECT COUNT(*) FROM cache WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            ).fetchone()[0]
            by_status = dict(
                conn.execute(
                    "SELECT status, COUNT(*) FROM cache GROUP BY status"
                ).fetchall()
            )
            by_source = dict(
                conn.execute(
                    "SELECT source, COUNT(*) FROM cache GROUP BY source"
                ).fetchall()
            )
        return {
            "total": total,
            "expired": expired,
            "active": total - expired,
            "by_status": by_status,
            "by_source": by_source,
        }
