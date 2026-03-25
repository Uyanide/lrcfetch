from abc import ABC, abstractmethod
from typing import Optional
from lrcfetch.models import TrackMeta, LyricResult


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
