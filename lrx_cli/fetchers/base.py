"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 02:33:26
Description: Base fetcher class and common interfaces
"""

from abc import ABC, abstractmethod
from typing import Optional

from ..models import TrackMeta, LyricResult


class BaseFetcher(ABC):
    @property
    @abstractmethod
    def source_name(self) -> str:
        """Name of the fetcher source."""
        pass

    @property
    def self_cached(self) -> bool:
        """True if this fetcher manages its own cache (skip per-source cache check)."""
        return False

    @abstractmethod
    def is_available(self, track: TrackMeta) -> bool:
        """Check if the fetcher is available for the given track (e.g. has required metadata)."""
        pass

    @abstractmethod
    async def fetch(
        self, track: TrackMeta, bypass_cache: bool = False
    ) -> Optional[LyricResult]:
        """Fetch lyrics for the given track. Returns None if unable to fetch."""
        pass
