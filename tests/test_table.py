import unicodedata

from sv.table import format_table


def display_width(value: str) -> int:
    width = 0
    for char in value:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def test_format_table_aligns_columns():
    output = format_table(
        ["Skill", "Repo", "Description"],
        [
            ["alpha", "Org/A", "Short."],
            ["longer-skill", "Org/B", "Longer description."],
        ],
    )

    assert output.splitlines() == [
        "Skill         Repo   Description",
        "------------  -----  -------------------",
        "alpha         Org/A  Short.",
        "longer-skill  Org/B  Longer description.",
    ]


def test_format_table_handles_no_rows():
    output = format_table(["Repo", "URL"], [])

    assert output.splitlines() == [
        "Repo  URL",
        "----  ---",
    ]


def test_format_table_wraps_long_cells_without_repeating_leading_columns():
    output = format_table(
        ["Skill", "Repo", "Description"],
        [["alpha", "Org/A", "This description wraps cleanly for narrow terminals."]],
        max_widths={"Description": 24},
    )

    assert output.splitlines() == [
        "Skill  Repo   Description",
        "-----  -----  ------------------------",
        "alpha  Org/A  This description wraps",
        "              cleanly for narrow",
        "              terminals.",
    ]


def test_format_table_escapes_control_characters_before_measuring_columns():
    output = format_table(
        ["Skill", "Description"],
        [["alpha", "Line one\nline two\x1b[2J"]],
    )

    assert output.splitlines() == [
        "Skill  Description",
        "-----  ---------------------------",
        "alpha  Line one\\x0aline two\\x1b[2J",
    ]


def test_format_table_breaks_long_words_to_honor_configured_widths():
    output = format_table(
        ["URL"],
        [["abcdefghijklmnop"]],
        max_widths={"URL": 6},
    )

    assert output.splitlines() == [
        "URL",
        "------",
        "abcdef",
        "ghijkl",
        "mnop",
    ]


def test_format_table_shrinks_single_column_to_fit_total_width():
    output = format_table(
        ["URL"],
        [["abcdefghijklmnop"]],
        max_table_width=6,
    )

    assert output.splitlines() == [
        "URL",
        "------",
        "abcdef",
        "ghijkl",
        "mnop",
    ]


def test_format_table_shrinks_columns_to_fit_total_width():
    output = format_table(
        ["Skill", "Repo", "Description"],
        [["alpha", "Org/A", "This description wraps to fit a narrow terminal."]],
        max_table_width=40,
        min_widths={"Description": 12},
    )

    lines = output.splitlines()
    assert all(len(line) <= 40 for line in lines)
    assert lines == [
        "Skill  Repo   Description",
        "-----  -----  --------------------------",
        "alpha  Org/A  This description wraps to",
        "              fit a narrow terminal.",
    ]


def test_format_table_uses_compact_separators_before_wrapping_headers():
    output = format_table(
        ["Skill", "Repo", "Description"],
        [["alpha", "Org", "Short"]],
        max_table_width=22,
    )

    assert output.splitlines()[0] == "Skill Repo Description"


def test_format_table_uses_compact_separators_for_very_narrow_widths():
    output = format_table(
        ["A", "B", "C"],
        [["alpha", "beta", "gamma"]],
        max_table_width=5,
    )

    assert output.splitlines() == [
        "A B C",
        "- - -",
        "a b g",
        "l e a",
        "p t m",
        "h a m",
        "a   a",
    ]


def test_format_table_hard_wraps_when_width_is_narrower_than_column_count():
    output = format_table(
        ["A", "B", "C"],
        [["alpha", "beta", "gamma"]],
        max_table_width=2,
    )

    assert all(len(line) <= 2 for line in output.splitlines())


def test_format_table_fits_wide_unicode_to_display_width():
    output = format_table(
        ["Skill", "Desc"],
        [["日本語", "abcdefghi"]],
        max_table_width=10,
        min_widths={"Desc": 4},
    )

    assert all(display_width(line) <= 10 for line in output.splitlines())
    assert "日本" in output
    assert "語" in output


def test_format_table_drops_overwide_characters_when_width_is_one():
    output = format_table(["言"], [["語"]], max_table_width=1)

    assert all(display_width(line) <= 1 for line in output.splitlines())
    assert output.splitlines() == ["?", "-", "?"]
