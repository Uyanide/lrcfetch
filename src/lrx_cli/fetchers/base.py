"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-25 02:33:26
Description: Base fetcher class and common interfaces.
"""

from abc import ABC, abstractmethod
from typing import Optional
from dataclasses import dataclass

from ..authenticators.base import BaseAuthenticator
from ..config import GeneralConfig
from ..models import CacheStatus, TrackMeta, LyricResult


@dataclass(frozen=True, slots=True)
class FetchResult:
    synced: Optional[LyricResult] = None
    unsynced: Optional[LyricResult] = None

    @staticmethod
    def from_not_found() -> "FetchResult":
        return FetchResult(
            synced=LyricResult(status=CacheStatus.NOT_FOUND, lyrics=None, source=None),
            unsynced=LyricResult(
                status=CacheStatus.NOT_FOUND, lyrics=None, source=None
            ),
        )

    @staticmethod
    def from_network_error() -> "FetchResult":
        return FetchResult(
            synced=LyricResult(
                status=CacheStatus.NETWORK_ERROR, lyrics=None, source=None
            ),
            unsynced=LyricResult(
                status=CacheStatus.NETWORK_ERROR, lyrics=None, source=None
            ),
        )


class BaseFetcher(ABC):
    def __init__(
        self, general: GeneralConfig, auth: Optional[BaseAuthenticator] = None
    ) -> None:
        self._general = general
        self._auth = auth

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
    async def fetch(self, track: TrackMeta, bypass_cache: bool = False) -> FetchResult:
        """Fetch lyrics for the given track. Returns None if unable to fetch."""
        pass
