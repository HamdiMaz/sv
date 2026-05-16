import subprocess
import sys

from sv.ui import CHOSEN_TTY_UI_APPROACH, PlainOutput, TtyUi, select_tty_items


def test_chosen_tty_ui_approach_prefers_stdlib_without_runtime_dependencies():
    assert CHOSEN_TTY_UI_APPROACH.name == "stdlib-inline"
    assert CHOSEN_TTY_UI_APPROACH.runtime_dependencies == ()
    assert "prompt-toolkit" in CHOSEN_TTY_UI_APPROACH.alternatives_considered
    assert "rich" in CHOSEN_TTY_UI_APPROACH.alternatives_considered


def test_plain_output_import_does_not_load_tty_selector_module():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import sv.ui; print('sv.selector' in sys.modules)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


def test_plain_output_formats_tables_without_tty_selector_dependency(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("TTY selector should not be used for plain output")

    monkeypatch.setattr("sv.ui.select_tty_items", fail_if_called)

    output = PlainOutput().table(["Skill", "Description"], [["alpha", "Alpha skill."]])

    assert output.splitlines() == [
        "Skill  Description",
        "-----  ------------",
        "alpha  Alpha skill.",
    ]


def test_tty_ui_delegates_selection_to_configured_selector():
    calls = []

    def selector(items, **kwargs):
        calls.append((items, kwargs))
        return [items[1]]

    selected = TtyUi(selector=selector).select_many(
        ["alpha", "beta"], item_label=lambda item: item.upper()
    )

    assert selected == ["beta"]
    assert len(calls) == 1
    assert calls[0][0] == ["alpha", "beta"]
    assert calls[0][1]["item_label"]("alpha") == "ALPHA"


def test_select_tty_items_uses_the_chosen_selector(monkeypatch):
    def selector(items, **kwargs):
        return [items[0]]

    monkeypatch.setattr("sv.selector.select_skills", selector)

    assert select_tty_items(["alpha", "beta"]) == ["alpha"]
