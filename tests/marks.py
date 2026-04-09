import pytest

from lrx_cli.config import load_config

_credentials = load_config().credentials

requires_spotify = pytest.mark.skipif(
    not _credentials.spotify_sp_dc,
    reason="requires credentials.spotify_sp_dc in config.toml",
)
requires_qq_music = pytest.mark.skipif(
    not _credentials.qq_music_api_url,
    reason="requires credentials.qq_music_api_url in config.toml",
)
requires_musixmatch_token = pytest.mark.skipif(
    not _credentials.musixmatch_usertoken,
    reason="requires credentials.musixmatch_usertoken in config.toml",
)
