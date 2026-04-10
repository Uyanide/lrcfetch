"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-31 06:09:11
Description: Metadata enrichment pipeline
"""

from __future__ import annotations

from loguru import logger

from .base import BaseEnricher
from .audio_tag import AudioTagEnricher
from .file_name import FileNameEnricher
from .musixmatch import MusixmatchSpotifyEnricher
from ..authenticators import BaseAuthenticator, MusixmatchAuthenticator
from ..models import TrackMeta

# Enrichers run in order; earlier ones have higher priority.
# There are only a few of them, so we can just call them sequentially without worrying about async concurrency or batching.


def create_enrichers(
    authenticators: dict[str, BaseAuthenticator],
) -> list[BaseEnricher]:
    """Instantiate all enrichers."""
    mxm_auth = authenticators["musixmatch"]
    assert isinstance(mxm_auth, MusixmatchAuthenticator)
    return [
        AudioTagEnricher(),
        FileNameEnricher(),
        MusixmatchSpotifyEnricher(mxm_auth),
    ]


async def enrich_track(track: TrackMeta, enrichers: list[BaseEnricher]) -> TrackMeta:
    """Run all enrichers and return a track with missing fields filled in.

    Each enricher sees the cumulative state (earlier enrichers' results
    are already applied).  A field is only set if it is currently None.
    """
    for enricher in enrichers:
        try:
            # Skip if all provided fields are already filled
            if all(
                getattr(track, field, None) is not None for field in enricher.provides
            ):
                continue

            result = await enricher.enrich(track)
        except Exception as e:
            logger.warning(f"Enricher {enricher.name} failed: {e}")
            continue
        if not result:
            continue
        # Only apply fields that are still None
        updates = {k: v for k, v in result.items() if getattr(track, k, None) is None}
        if updates:
            for k, v in updates.items():
                setattr(track, k, v)
    return track
