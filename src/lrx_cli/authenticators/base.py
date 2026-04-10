"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-04-05 03:18:14
Description: Base class for credential authenticators.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..cache import CacheEngine
from ..config import CredentialConfig, GeneralConfig


class BaseAuthenticator(ABC):
    """Manages obtaining, caching, and refreshing a credential for one provider."""

    def __init__(
        self, cache: CacheEngine, credentials: CredentialConfig, general: GeneralConfig
    ) -> None:
        self._cache = cache
        self._credentials = credentials
        self._general = general

    @property
    @abstractmethod
    def name(self) -> str: ...

    def is_configured(self) -> bool:
        """True if the prerequisite config (e.g. env var) is present.

        Default is True — authenticators that can obtain credentials anonymously
        should not override this.
        """
        return True

    @abstractmethod
    async def authenticate(self) -> Optional[str]:
        """Return current valid credential string, refreshing if needed.

        Returns None if unavailable (misconfigured or network failure).
        """
        ...
