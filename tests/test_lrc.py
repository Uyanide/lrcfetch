from __future__ import annotations

from lrx_cli.lrc import (
    detect_sync_status,
    is_synced,
    normalize_tags,
    normalize_unsynced,
    to_plain,
)
from lrx_cli.models import CacheStatus


def test_normalize_tags_supports_all_raw_time_formats() -> None:
    raw = "\n".join(
        [
            "[00:01]a",
            "[00:02.3]b",
            "[00:03.45]c",
            "[00:04.678]d",
            "[00:05:999]e",
        ]
    )

    normalized = normalize_tags(raw)

    assert normalized == "\n".join(
        [
            "[00:01.00]a",
            "[00:02.30]b",
            "[00:03.45]c",
            "[00:04.68]d",
            "[00:05.99]e",
        ]
    )


def test_normalize_tags_keeps_non_timed_lines_trimmed_and_unchanged() -> None:
    raw = "  plain line  \n\n  [ar:Meta Header]  "

    normalized = normalize_tags(raw)

    assert normalized == "plain line\n\n[ar:Meta Header]"


def test_normalize_tags_removes_word_sync_patterns() -> None:
    raw = (
        "[00:01.00]<00:01>hello\n"
        "[00:02.00]<00:02.3>world\n"
        "[00:03.00]<00:03.45>foo\n"
        "[00:04.00]<00:04:678>bar\n"
        "[00:05.00]<1,2,3>baz"
    )

    normalized = normalize_tags(raw)

    assert normalized == "\n".join(
        [
            "[00:01.00]hello",
            "[00:02.00]world",
            "[00:03.00]foo",
            "[00:04.00]bar",
            "[00:05.00]baz",
        ]
    )


def test_normalize_tags_keeps_midline_timestamps_as_is() -> None:
    raw = "[00:01.00]Lyric [00:02.00]line"

    normalized = normalize_tags(raw)

    assert normalized == "[00:01.00]Lyric [00:02.00]line"


def test_normalize_tags_applies_positive_and_negative_offset_per_spec() -> None:
    positive = normalize_tags("[offset:+1000]\n[00:10.00]line")
    negative = normalize_tags("[offset:-500]\n[00:10.00]line")

    assert positive == "[00:09.00]line"
    assert negative == "[00:10.50]line"


def test_normalize_tags_accepts_leading_spaces_and_tabs_before_tags() -> None:
    raw = "\t   [00:01.2] hello"

    normalized = normalize_tags(raw)

    assert normalized == "[00:01.20]hello"


def test_normalize_tags_handles_consecutive_start_tags_with_spaces_between() -> None:
    raw = "[00:01]   [00:02.3]    chorus"

    normalized = normalize_tags(raw)

    assert normalized == "[00:01.00][00:02.30]chorus"


def test_normalize_tags_preserves_non_leading_raw_like_tags() -> None:
    raw = "intro [00:01]line"

    normalized = normalize_tags(raw)

    assert normalized == "intro [00:01]line"


def test_normalize_tags_removes_offset_tag_line_even_without_lyrics() -> None:
    raw = "[offset:+500]"

    normalized = normalize_tags(raw)

    assert normalized == ""


def test_is_synced_and_detect_sync_status_follow_non_zero_rule() -> None:
    plain_text = "just some lyrics\nwithout tags"
    unsynced_text = "[00:00.00]a\n[00:00.00]b"
    synced_text = "[00:00.00]a\n[00:01.00]b"

    assert is_synced(plain_text) is False
    assert detect_sync_status(plain_text) is CacheStatus.SUCCESS_UNSYNCED

    assert is_synced(unsynced_text) is False
    assert detect_sync_status(unsynced_text) is CacheStatus.SUCCESS_UNSYNCED

    assert is_synced(synced_text) is True
    assert detect_sync_status(synced_text) is CacheStatus.SUCCESS_SYNCED


def test_normalize_unsynced_covers_documented_blank_and_tag_rules() -> None:
    lyrics = "\n[00:12.34]first\nsecond\n\n[00:00.00]third"

    normalized = normalize_unsynced(lyrics)

    assert normalized == "\n".join(
        [
            "[00:00.00]first",
            "[00:00.00]second",
            "[00:00.00]",
            "[00:00.00]third",
        ]
    )


def test_to_plain_duplicates_lines_by_leading_repeated_timestamps() -> None:
    text = "\n".join(
        [
            "[00:01.00][00:02.00]hello",
            "[00:03.00]world",
            "no-tag-line",
            "[00:00.00]zero-only",
        ]
    )

    plain = to_plain(text)

    # In synced mode, lines with standard tags are kept (including [00:00.00]),
    # while lines without leading standard tags are ignored.
    assert plain == "\n".join(["hello", "hello", "world", "zero-only"])


def test_to_plain_deduplicate_collapses_only_consecutive_equals() -> None:
    text = "\n".join(
        [
            "[00:01.00][00:02.00]hello",
            "[00:03.00]hello",
            "[00:04.00]",
            "[00:05.00]",
            "[00:06.00]world",
            "[00:07.00]hello",
        ]
    )

    plain = to_plain(text, deduplicate=True)

    assert plain == "\n".join(["hello", "", "world", "hello"])


def test_to_plain_fallback_for_non_synced_text_strips_start_tags() -> None:
    text = "\n".join(["[ar:Artist]", "[00:00.00]only-zero", "plain line"])

    plain = to_plain(text)

    assert plain == "only-zero\nplain line"
