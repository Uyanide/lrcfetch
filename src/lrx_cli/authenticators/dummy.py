"""
Author: Uyanide pywang0608@foxmail.com
Date: 2026-04-05 03:36:44
Description: A dummy authenticator that does nothing and always reports as configured.
"""

from .base import BaseAuthenticator


class DummyAuthenticator(BaseAuthenticator):
    @property
    def name(self) -> str:
        return "dummy"

    def is_configured(self) -> bool:
        return True

    async def authenticate(self) -> None:
        return None
