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


def test_format_table_wraps_repo_like_values_at_slash_boundaries():
    output = format_table(
        ["Repo"],
        [["OrgName/SkillRepo"]],
        max_widths={"Repo": 10},
    )

    assert output.splitlines() == [
        "Repo",
        "----------",
        "OrgName/",
        "SkillRepo",
    ]


def test_format_table_wraps_qualified_skill_values_at_colon_boundaries():
    output = format_table(
        ["Add as"],
        [["Org/Repo:alpha"]],
        max_widths={"Add as": 9},
    )

    assert output.splitlines() == [
        "Add as",
        "---------",
        "Org/Repo:",
        "alpha",
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


def test_format_table_measures_emoji_by_display_width():
    output = format_table(["Name"], [["😀😄😁"]], max_table_width=5)

    lines = output.splitlines()
    assert all(display_width(line) <= 5 for line in lines)
    assert lines == [
        "Name",
        "-----",
        "😀😄",
        "😁",
    ]


def test_format_table_wraps_multiple_wide_symbols_at_narrow_widths():
    output = format_table(["Sym", "Value"], [["😀😄😁", "abc"]], max_table_width=6)

    lines = output.splitlines()
    assert lines == [
        "Sy  Va",
        "m   lu",
        "    e",
        "--  --",
        "😀  ab",
        "😄  c",
        "😁",
    ]
    assert all(display_width(line) <= 6 for line in lines)


def test_format_table_handles_combining_characters_in_narrow_tables():
    output = format_table(["Base", "Desc"], [["Cafe\u0301", "ab"]], max_table_width=4)

    lines = output.splitlines()
    assert all(display_width(line) <= 4 for line in lines)
    assert lines == [
        "B  D",
        "a  e",
        "s  s",
        "e  c",
        "-  -",
        "C  a",
        "a  b",
        "f",
        "é",
    ]


def test_format_table_handles_fewer_and_extra_cells_per_row():
    sparse_row = format_table(
        ["Skill", "Repo", "Description"],
        [["alpha", "Org/A"]],
        max_table_width=30,
    )
    dense_row = format_table(
        ["A", "B"],
        [["alpha", "beta", "gamma", "delta"]],
        max_table_width=20,
    )

    assert sparse_row.splitlines() == [
        "Skill  Repo   Description",
        "-----  -----  -----------",
        "alpha  Org/A",
    ]
    assert dense_row.splitlines() == [
        "A      B",
        "-----  ----",
        "alpha  beta",
    ]


def test_format_table_wraps_sparse_rows_at_tight_widths():
    output = format_table(
        ["Skill", "Repo", "Description"],
        [["alpha"]],
        max_table_width=6,
    )

    assert output.splitlines() == [
        "S R De",
        "k e sc",
        "i p ri",
        "l o pt",
        "l   io",
        "    n",
        "- - --",
        "a",
        "l",
        "p",
        "h",
        "a",
    ]


def test_format_table_ignores_extra_cells_at_tight_widths():
    output = format_table(
        ["A", "B"],
        [["alpha", "beta", "gamma", "delta"]],
        max_table_width=6,
    )

    assert output.splitlines() == [
        "A   B",
        "--  --",
        "al  be",
        "ph  ta",
        "a",
    ]


def test_format_table_treats_zero_or_negative_max_table_width_as_unbounded_with_wide_and_unicode_cells():
    for max_table_width in (0, -1, -2):
        output = format_table(
            ["Emoji", "Wide"],
            [["😀", "日本語"]],
            max_table_width=max_table_width,
        )

        assert output.splitlines() == [
            "EmojiWide",
            "-----------",
            "😀   日本語",
        ]
        assert all("?" not in line for line in output.splitlines())


def test_format_table_handles_empty_string_cells_while_wrapping():
    output = format_table(["A", "B", "C"], [["a", "", "abcdef"]], max_table_width=6)

    assert output.splitlines() == [
        "A B C",
        "- - --",
        "a   ab",
        "    cd",
        "    ef",
    ]
    assert all(display_width(line) <= 6 for line in output.splitlines())


def test_format_table_accepts_column_index_width_overrides():
    output = format_table(
        ["A", "B"],
        [["abcdef", "xy"]],
        max_widths={0: 3},
    )

    assert output.splitlines() == [
        "A    B",
        "---  --",
        "abc  xy",
        "def",
    ]


def test_format_table_replaces_overwide_character_after_current_text():
    output = format_table(["V"], [["a語b"]], max_widths={"V": 1})

    assert output.splitlines() == [
        "V",
        "-",
        "a",
        "?",
        "b",
    ]


def test_format_table_preserves_combining_mark_when_breaking_long_text():
    output = format_table(["Value"], [["abce\u0301def"]], max_widths={"Value": 4})

    assert output.splitlines() == [
        "Value",
        "-----",
        "abcéd",
        "ef",
    ]


def test_format_table_treats_zero_or_negative_max_table_width_as_unbounded():
    for max_table_width in (0, -1, -2):
        output = format_table(
            ["A", "B", "C"],
            [["alpha", "beta", "gamma"]],
            max_table_width=max_table_width,
        )

        assert output.splitlines() == [
            "A    B   C",
            "--------------",
            "alphabetagamma",
        ]
        assert " " not in output.splitlines()[2]
