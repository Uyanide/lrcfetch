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

    @abstractmethod
    def fetch(self, track: TrackMeta) -> Optional[LyricResult]:
        """Fetch lyrics for the given track. Returns None if unable to fetch."""
        pass
