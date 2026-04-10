"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-04-02 05:24:27
Description: Shared text normalization utilities for fuzzy matching.
             Used by cache key generation, cache search, and candidate selection scoring.
"""

from __future__ import annotations

import re
import unicodedata

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


def normalize_for_match(s: str) -> str:
    """Normalize a string for fuzzy comparison.

    Lowercases, NFKC-normalizes (fullwidth → halfwidth), strips punctuation,
    and collapses whitespace.
    """
    s = unicodedata.normalize("NFKC", s).lower()
    s = _FEAT_RE.sub("", s)
    s = _PUNCT_RE.sub(" ", s)
    s = _SPACE_RE.sub(" ", s).strip()
    return s


def normalize_artist(s: str) -> str:
    """Normalize an artist string: split by separators, normalize each, sort.

    Splits first (on /, &, ;, ×, 、, vs., x), then strips feat./ft./featuring
    from each part individually, so 'A feat. C / B' → ['a', 'b'] not just ['a'].
    """
    s = unicodedata.normalize("NFKC", s).lower()
    parts = _ARTIST_SEP_RE.split(s)
    normed = sorted(
        {normalize_for_match(p) for p in parts if _FEAT_RE.sub("", p).strip()}
    )
    return "\0".join(normed) if normed else normalize_for_match(s)
