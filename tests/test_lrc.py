from __future__ import annotations

from lrx_cli.lrc import LRCData
from lrx_cli.models import CacheStatus


def _normalize(text: str) -> str:
    return str(LRCData(text))


def test_time_tag_formats_are_normalized() -> None:
    raw = "\n".join(
        [
            "[00:01]a",
            "[00:02.3]b",
            "[00:03.45]c",
            "[00:04.678]d",
            "[00:05:999]e",
        ]
    )

    normalized = _normalize(raw)

    assert normalized == "\n".join(
        [
            "[00:01.00]a",
            "[00:02.30]b",
            "[00:03.45]c",
            "[00:04.68]d",
            "[00:05.99]e",
        ]
    )


def test_non_timed_lines_are_kept_as_lyrics() -> None:
    raw = "  plain line  \n\n  other line  "

    normalized = _normalize(raw)

    assert normalized == "plain line\n\nother line"


def test_word_sync_tags_are_parsed_and_export_controlled() -> None:
    raw = "[00:01.00]<00:01>he <00:01.50>llo\n[00:02.00]plain"

    data = LRCData(raw)

    assert data.to_text(include_word_sync=False) == "[00:01.00]he llo\n[00:02.00]plain"
    assert (
        data.to_text(include_word_sync=True)
        == "[00:01.00]<00:01.00>he <00:01.50>llo\n[00:02.00]plain"
    )


def test_midline_line_tags_are_kept_as_plain_text() -> None:
    raw = "[00:01.00]Lyric [00:02.00]line"

    normalized = _normalize(raw)

    assert normalized == "[00:01.00]Lyric [00:02.00]line"


def test_leading_spaces_before_first_time_tag_are_trimmed() -> None:
    raw = "\t   [00:01.2] hello"

    normalized = _normalize(raw)

    assert normalized == "[00:01.20]hello"


def test_normalize_tags_handles_consecutive_start_tags_with_spaces_between() -> None:
    raw = "[00:01]   [00:02.3]    chorus"

    data = LRCData(raw)
    assert len(data.lines) == 1
    assert str(data) == "[00:01.00][00:02.30]chorus"
    assert data.to_plain() == "chorus\nchorus"


def test_non_leading_time_like_text_is_plain_lyric() -> None:
    raw = "intro [00:01]line"

    normalized = _normalize(raw)

    assert normalized == "intro [00:01]line"


def test_is_synced_and_detect_sync_status_follow_non_zero_rule() -> None:
    plain_text = "just some lyrics\nwithout tags"
    unsynced_text = "[00:00.00]a\n[00:00.00]b"
    synced_text = "[00:00.00]a\n[00:01.00]b"

    assert LRCData(plain_text).is_synced() is False
    assert LRCData(plain_text).detect_sync_status() is CacheStatus.SUCCESS_UNSYNCED

    assert LRCData(unsynced_text).is_synced() is False
    assert LRCData(unsynced_text).detect_sync_status() is CacheStatus.SUCCESS_UNSYNCED

    assert LRCData(synced_text).is_synced() is True
    assert LRCData(synced_text).detect_sync_status() is CacheStatus.SUCCESS_SYNCED


def test_normalize_unsynced_covers_documented_blank_and_tag_rules() -> None:
    lyrics = "\n[00:12.34]first\nsecond\n\n[00:00.00]third"

    normalized = str(LRCData(lyrics).normalize_unsynced())

    assert normalized == "\n".join(
        [
            "[00:00.00]first",
            "[00:00.00]second",
            "[00:00.00]",
            "[00:00.00]third",
        ]
    )


def test_normalize_unsynced_preserves_doc_tags_and_middle_blanks() -> None:
    text = "\n".join(["[ar:Artist]", "", "[00:03.00]line", "[ti:Song]", "", " tail "])

    normalized = LRCData(text).normalize_unsynced()

    assert normalized.tags == {"ar": "Artist", "ti": "Song"}
    assert str(normalized) == "\n".join(
        [
            "[ar:Artist]",
            "[00:00.00]line",
            "[ti:Song]",
            "[00:00.00]",
            "[00:00.00]tail",
        ]
    )


def test_normalize_unsynced_strips_word_sync_markup_from_lyric_text() -> None:
    text = "[00:02.00]<00:01.00>he <00:01.50>llo"

    normalized = str(LRCData(text).normalize_unsynced())

    assert normalized == "[00:00.00]he llo"


def test_normalize_unsynced_result_is_always_unsynced() -> None:
    text = "[00:05.00]a\n[00:10.00]b"

    normalized = LRCData(text).normalize_unsynced()

    assert normalized.is_synced() is False
    assert normalized.detect_sync_status() is CacheStatus.SUCCESS_UNSYNCED


def test_to_plain_duplicates_lines_for_multi_line_times() -> None:
    text = "\n".join(
        [
            "[00:02.00][00:01.00]hello",
            "[00:03.00]world",
            "no-tag-line",
            "[00:00.00]zero-only",
        ]
    )

    plain = LRCData(text).to_plain()

    # In synced mode, lines with standard tags are kept (including [00:00.00]),
    # lines without leading standard tags are ignored, and output is sorted by tag timestamp.
    assert plain == "\n".join(["zero-only", "hello", "hello", "world"])


def test_to_plain_sorts_lines_by_timestamp_across_lines() -> None:
    text = "\n".join(
        [
            "[00:05.00]late",
            "[00:01.00]early",
            "[00:03.00]middle",
        ]
    )

    plain = LRCData(text).to_plain()

    assert plain == "\n".join(["early", "middle", "late"])


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

    plain = LRCData(text).to_plain(deduplicate=True)

    assert plain == "\n".join(["hello", "", "world", "hello"])


def test_to_plain_fallback_for_non_synced_text_strips_start_tags() -> None:
    text = "\n".join(["[ar:Artist]", "[00:00.00]only-zero", "plain line"])

    plain = LRCData(text).to_plain()

    assert plain == "only-zero\nplain line"


def test_to_plain_trims_leading_and_trailing_blank_lines() -> None:
    text = "\n\n[00:01.00]line1\n\n[00:01.00]\n[00:02.00]line2\nline3\n   \n"

    plain = LRCData(text).to_plain()

    assert plain == "line1\n\nline2"


def test_reformat_pipeline_trims_outer_blanks_and_preserves_inner_blanks() -> None:
    text = "\n\n[00:01]a\n\n[00:02]b\n\n"

    normalized = str(LRCData(text))

    assert normalized == "[00:01.00]a\n\n[00:02.00]b"


def test_single_doc_tag_line_is_preserved_and_registered() -> None:
    data = LRCData("[ar:Artist]\n[00:01.00]line")

    assert data.tags == {"ar": "Artist"}
    assert len(data.lines) == 2
    assert str(data) == "[ar:Artist]\n[00:01.00]line"
    assert data.to_plain() == "line"


def test_multiple_doc_tags_on_one_line_are_plain_lyrics() -> None:
    data = LRCData("[ar:Artist][ti:Song]")

    assert data.tags == {}
    assert len(data.lines) == 1
    assert data.lines[0].text == "[ar:Artist][ti:Song]"


def test_doc_tag_after_lyrics_is_treated_as_lyrics() -> None:
    data = LRCData("[00:01.00]line\n[ar:Artist]")

    assert data.tags == {"ar": "Artist"}
    assert len(data.lines) == 2
    assert str(data) == "[00:01.00]line\n[ar:Artist]"
    assert data.to_plain() == "line"


def test_unknown_lines_before_lyrics_are_preserved_and_do_not_start_lyrics() -> None:
    data = LRCData("comment line\n[ar:Artist]\n[00:01.00]line")

    assert data.tags == {"ar": "Artist"}
    assert len(data.lines) == 3
    assert str(data) == "comment line\n[ar:Artist]\n[00:01.00]line"
    assert data.to_plain() == "line"


def test_to_plain_excludes_doc_tags_but_keeps_lyrics() -> None:
    data = LRCData("[ar:Artist]\n[00:01.00]line\n[ti:Song]\nplain")

    assert data.to_plain() == "line"


def test_non_space_between_line_tags_stops_tag_parsing() -> None:
    data = LRCData("[00:01.00]x[00:02.00]tail")

    assert len(data.lines) == 1
    assert str(data) == "[00:01.00]x[00:02.00]tail"
    assert data.to_plain() == "x[00:02.00]tail"


def test_line_only_time_tag_is_valid_empty_lyric() -> None:
    data = LRCData("[00:01.00]")

    assert len(data.lines) == 1
    assert str(data) == "[00:01.00]"
    assert data.to_plain() == ""


def test_word_sync_markup_only_changes_output_when_enabled() -> None:
    a = LRCData("[00:01.00]<00:00.50>lyric")
    b = LRCData("[00:01.00]lyric")

    assert a.to_text(include_word_sync=False) == "[00:01.00]lyric"
    assert b.to_text(include_word_sync=False) == "[00:01.00]lyric"
    assert a.to_text(include_word_sync=True) == "[00:01.00]<00:00.50>lyric"
    assert b.to_text(include_word_sync=True) == "[00:01.00]lyric"


def test_word_sync_line_with_empty_tail_keeps_word_tag_only_when_enabled() -> None:
    data = LRCData("[00:01.00]<00:02.00>")

    assert data.to_text(include_word_sync=False) == "[00:01.00]"
    assert data.to_text(include_word_sync=True) == "[00:01.00]<00:02.00>"


def test_to_text_plain_true_matches_to_plain_output() -> None:
    data = LRCData("[00:02.00]b\n[00:01.00]a")

    assert data.to_text(plain=True) == data.to_plain()


def test_duplicate_doc_tag_key_last_value_wins_but_lines_are_kept() -> None:
    data = LRCData("[ar:First]\n[ar:Second]\n[00:01.00]line")

    assert data.tags == {"ar": "Second"}
    assert len(data.lines) == 3
    assert str(data).startswith("[ar:First]\n[ar:Second]\n")


def test_to_plain_for_doc_only_text_is_empty() -> None:
    data = LRCData("[ar:Artist]\n[ti:Song]")

    assert data.to_plain() == ""
