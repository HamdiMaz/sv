import io
import subprocess
import sys

from sv.ui import CHOSEN_TTY_UI_APPROACH, PlainOutput, TtyUi, select_tty_items


class _TtyStringIO(io.StringIO):
    def isatty(self):
        return True


def test_chosen_tty_ui_approach_prefers_stdlib_without_runtime_dependencies():
    assert CHOSEN_TTY_UI_APPROACH.name == "stdlib-inline"
    assert CHOSEN_TTY_UI_APPROACH.runtime_dependencies == ()
    assert "prompt-toolkit" in CHOSEN_TTY_UI_APPROACH.alternatives_considered
    assert "rich" in CHOSEN_TTY_UI_APPROACH.alternatives_considered


def test_loading_reporter_does_not_write_when_disabled():
    from sv.ui import LoadingReporter

    stream = io.StringIO()
    reporter = LoadingReporter(
        stream,
        enabled=False,
        frames=("|",),
        interval_seconds=60.0,
    )

    with reporter.operation("Refreshing source repo Org/Skills"):
        reporter.print_line("warning: hidden from spinner test")

    assert stream.getvalue() == "warning: hidden from spinner test\n"


def test_loading_reporter_writes_spinner_and_clears_on_tty():
    from sv.ui import LoadingReporter

    stream = _TtyStringIO()
    reporter = LoadingReporter(
        stream,
        enabled=True,
        frames=("|",),
        interval_seconds=60.0,
    )

    with reporter.operation("Refreshing source repo Org/Skills"):
        pass

    output = stream.getvalue()
    rendered = "| Refreshing source repo Org/Skills"
    assert f"\r{rendered}" in output
    assert output.endswith("\r" + (" " * len(rendered)) + "\r")


def test_loading_reporter_print_line_clears_and_redraws_active_spinner():
    from sv.ui import LoadingReporter

    stream = _TtyStringIO()
    reporter = LoadingReporter(
        stream,
        enabled=True,
        frames=("|",),
        interval_seconds=60.0,
    )

    with reporter.operation("Refreshing source repo Org/Skills"):
        reporter.print_line("warning: using stale cached metadata")

    output = stream.getvalue()
    rendered = "| Refreshing source repo Org/Skills"
    clear_sequence = "\r" + (" " * len(rendered)) + "\r"
    assert clear_sequence + "warning: using stale cached metadata\n" in output
    assert output.endswith(clear_sequence)


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


def test_tty_ui_delegates_table_browsing_to_configured_browser():
    calls = []

    def browser(headers, rows, **kwargs):
        calls.append((headers, rows, kwargs))
        return ["alpha"]

    selected = TtyUi(table_browser=browser).browse_table(
        ["Skill"],
        [["alpha"]],
        key_help="a action",
    )

    assert selected == ["alpha"]
    assert calls == [
        (["Skill"], [["alpha"]], {"key_help": "a action"})
    ]


def test_browse_tty_table_uses_the_chosen_browser(monkeypatch):
    from sv import ui as ui_module

    calls = []

    def browser(headers, rows, **kwargs):
        calls.append((headers, rows, kwargs))
        return None

    monkeypatch.setattr(ui_module, "TtyUi", lambda: TtyUi(table_browser=browser))

    assert ui_module.browse_tty_table(["Skill"], [["alpha"]], clear_on_exit=True) is None
    assert calls == [
        (["Skill"], [["alpha"]], {"clear_on_exit": True})
    ]


def test_plain_output_and_tty_ui_are_separate_adapter_instances():
    plain = PlainOutput()
    tty = TtyUi(
        selector=lambda items, **kwargs: list(items),
        table_browser=lambda headers, rows, **kwargs: None,
    )

    assert plain.table(["A"], [["B"]]).splitlines()[0] == "A"
    assert tty.select_many(["alpha"]) == ["alpha"]
    assert tty.browse_table(["Skill"], [["alpha"]]) is None
