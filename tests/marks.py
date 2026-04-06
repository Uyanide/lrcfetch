import os

import pytest

requires_spotify = pytest.mark.skipif(
    not os.environ.get("SPOTIFY_SP_DC"),
    reason="requires SPOTIFY_SP_DC",
)
requires_qq_music = pytest.mark.skipif(
    not os.environ.get("QQ_MUSIC_API_URL"),
    reason="requires QQ_MUSIC_API_URL",
)
requires_musixmatch_token = pytest.mark.skipif(
    not os.environ.get("MUSIXMATCH_USERTOKEN"),
    reason="requires MUSIXMATCH_USERTOKEN",
)
