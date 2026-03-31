"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 10:18:03
Description: SQLite-based lyric cache with per-source storage and TTL expiration
"""

import re
import sqlite3
import hashlib
import time
import unicodedata
from typing import Optional
from loguru import logger

from .config import DB_PATH, DURATION_TOLERANCE_MS
from .models import TrackMeta, LyricResult, CacheStatus

# Punctuation to strip for fuzzy matching (ASCII + fullwidth + CJK brackets/symbols)
_PUNCT_RE = re.compile(
    r"[~!@#$%^&*()_+\-=\[\]{}|;:'\",.<>?/\\`"
    r"～！＠＃＄％＾＆＊（）＿＋－＝【】｛｝｜；：＇＂，。＜＞？／＼｀"
    r"「」『』《》〈〉〔〕·•‥…—–]"
)
_SPACE_RE = re.compile(r"\s+")
# feat./ft./featuring and everything after (case-insensitive, word boundary)
_FEAT_RE = re.compile(r"\s*(?:\bfeat\.?\b|\bft\.?\b|\bfeaturing\b).*", re.IGNORECASE)
# Multi-artist separators: /, &, ×, x (surrounded by spaces), ;, 、, vs.
_ARTIST_SEP_RE = re.compile(r"\s*(?:[/&;×、]|\bvs\.?\b|\bx\b)\s*", re.IGNORECASE)


def _normalize_for_match(s: str) -> str:
    """Normalize a string for fuzzy comparison.

    Lowercases, NFKC-normalizes (fullwidth → halfwidth), strips punctuation,
    and collapses whitespace.
    """
    s = unicodedata.normalize("NFKC", s).lower()
    s = _FEAT_RE.sub("", s)
    s = _PUNCT_RE.sub(" ", s)
    s = _SPACE_RE.sub(" ", s).strip()
    return s


def _normalize_artist(s: str) -> str:
    """Normalize an artist string: split by separators, normalize each, sort.

    Splits first (on /, &, ;, ×, 、, vs., x), then strips feat./ft./featuring
    from each part individually, so 'A feat. C / B' → ['a', 'b'] not just ['a'].
    """
    s = unicodedata.normalize("NFKC", s).lower()
    parts = _ARTIST_SEP_RE.split(s)
    normed = sorted(
        {_normalize_for_match(p) for p in parts if _FEAT_RE.sub("", p).strip()}
    )
    return "\0".join(normed) if normed else _normalize_for_match(s)


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
                    album TEXT,
                    length INTEGER
                )
            """)
            # Migration: add length column if missing
            cols = {r[1] for r in conn.execute("PRAGMA table_info(cache)").fetchall()}
            if "length" not in cols:
                conn.execute("ALTER TABLE cache ADD COLUMN length INTEGER")
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
                "SELECT status, lyrics, source, expires_at, length FROM cache WHERE key = ?",
                (key,),
            ).fetchone()

            if not row:
                logger.debug(f"Cache miss: {source} / {track.display_name()}")
                return None

            status_str, lyrics, src, expires_at, cached_length = row

            # Check TTL expiration
            if expires_at and expires_at < int(time.time()):
                logger.debug(f"Cache expired: {source} / {track.display_name()}")
                conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                conn.commit()
                return None

            # Backfill length if the cached row is missing it
            if cached_length is None and track.length is not None:
                conn.execute(
                    "UPDATE cache SET length = ? WHERE key = ?",
                    (track.length, key),
                )
                conn.commit()

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
                    artist, title, album, length)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    track.length,
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

    # Exact cross-source search

    def find_best_positive(self, track: TrackMeta) -> Optional[LyricResult]:
        """Find the best positive (synced/unsynced) cache entry for *track*.

        Uses exact metadata match (artist + title + album) across all sources.
        Returns synced if available, otherwise unsynced, or None.
        """
        conditions, params = self._track_where(track)
        if not conditions:
            return None

        now = int(time.time())
        conditions.append("status IN (?, ?)")
        params.extend(
            [CacheStatus.SUCCESS_SYNCED.value, CacheStatus.SUCCESS_UNSYNCED.value]
        )
        conditions.append("(expires_at IS NULL OR expires_at > ?)")
        params.append(str(now))

        where = " AND ".join(conditions)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT status, lyrics, source FROM cache WHERE {where} "
                "ORDER BY CASE status WHEN ? THEN 0 ELSE 1 END LIMIT 1",
                params + [CacheStatus.SUCCESS_SYNCED.value],
            ).fetchall()

        if not rows:
            return None

        row = dict(rows[0])
        return LyricResult(
            status=CacheStatus(row["status"]),
            lyrics=row["lyrics"],
            source="cache-search",
        )

    # Fuzzy search

    def search_by_meta(
        self,
        artist: Optional[str],
        title: Optional[str],
        length: Optional[int] = None,
    ) -> list[dict]:
        """Search cache for lyrics matching artist/title with fuzzy normalization.

        Ignores album and source. Only returns positive results (synced/unsynced)
        that have not expired. When *length* is provided, filters by duration
        tolerance and sorts by closest match.
        """
        if not title:
            return []

        now = int(time.time())
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM cache
                   WHERE status IN (?, ?)
                     AND (expires_at IS NULL OR expires_at > ?)""",
                (
                    CacheStatus.SUCCESS_SYNCED.value,
                    CacheStatus.SUCCESS_UNSYNCED.value,
                    now,
                ),
            ).fetchall()

        norm_title = _normalize_for_match(title)
        norm_artist = _normalize_artist(artist) if artist else None

        matches: list[dict] = []
        for row in rows:
            row_dict = dict(row)
            # Title must match
            row_title = row_dict.get("title") or ""
            if _normalize_for_match(row_title) != norm_title:
                continue
            # Artist must match if provided
            if norm_artist:
                row_artist = row_dict.get("artist") or ""
                if _normalize_artist(row_artist) != norm_artist:
                    continue
            matches.append(row_dict)

        # Duration filtering
        if length is not None and matches:
            scored = []
            for m in matches:
                row_len = m.get("length")
                if row_len is not None:
                    diff = abs(row_len - length)
                    if diff <= DURATION_TOLERANCE_MS:
                        scored.append((diff, m))
                else:
                    # No duration info in cache — still a candidate but lower priority
                    scored.append((DURATION_TOLERANCE_MS, m))
            scored.sort(
                key=lambda x: (
                    x[0],
                    x[1].get("status") != CacheStatus.SUCCESS_SYNCED.value,
                )
            )
            matches = [m for _, m in scored]

        return matches

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
