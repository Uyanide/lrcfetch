"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-03-31 06:08:16
Description: Base class for metadata enrichers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..models import TrackMeta


class BaseEnricher(ABC):
    """Attempts to fill missing fields on a TrackMeta.

    Each enricher inspects the track, and returns a dict of field names
    to values for any fields it can provide.  Only fields that are
    currently ``None`` on the track will actually be applied.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def provides(self) -> set[str]: ...

    @abstractmethod
    async def enrich(self, track: TrackMeta) -> Optional[dict]:
        """Return a dict of {field_name: value} for fields this enricher can fill.

        Return None or an empty dict if nothing can be contributed.
        """
        ...
