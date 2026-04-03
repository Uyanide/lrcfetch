"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-31 06:09:11
Description: Metadata enrichment pipeline
"""

from loguru import logger

from .base import BaseEnricher
from .audio_tag import AudioTagEnricher
from .file_name import FileNameEnricher
from ..models import TrackMeta

# Enrichers run in order; earlier ones have higher priority.
_ENRICHERS: list[BaseEnricher] = [
    AudioTagEnricher(),
    FileNameEnricher(),
]


def enrich_track(track: TrackMeta) -> TrackMeta:
    """Run all enrichers and return a track with missing fields filled in.

    Each enricher sees the cumulative state (earlier enrichers' results
    are already applied).  A field is only set if it is currently None.
    """
    for enricher in _ENRICHERS:
        try:
            # Skip if all provided fields are already filled
            if all(
                getattr(track, field, None) is not None for field in enricher.provides
            ):
                continue

            result = enricher.enrich(track)
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
