import pytest

from lrx_cli.config import enable_debug

enable_debug()


@pytest.fixture
def no_credentials(monkeypatch):
    """Clear all credential env vars so only anonymous fetchers are active."""
    monkeypatch.delenv("SPOTIFY_SP_DC", raising=False)
    monkeypatch.delenv("QQ_MUSIC_API_URL", raising=False)
    monkeypatch.delenv("MUSIXMATCH_USERTOKEN", raising=False)
