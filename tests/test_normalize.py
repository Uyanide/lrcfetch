from __future__ import annotations

from lrx_cli.normalize import normalize_for_match, normalize_artist


def test_normalize_for_match_covers_nfkc_punct_feat_and_whitespace() -> None:
    text = "  Ｔｅｓｔ！  feat. SOMEONE  "

    normalized = normalize_for_match(text)

    assert normalized == "test"


def test_normalize_artist_splits_separators_and_sorts_parts() -> None:
    artist = "B / A feat. C; D vs. E × F 、 G"

    normalized = normalize_artist(artist)

    assert normalized == "a\0b\0d\0e\0f\0g"
