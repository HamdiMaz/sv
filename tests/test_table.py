from sv.table import format_table


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
