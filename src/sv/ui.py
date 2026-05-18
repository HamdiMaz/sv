from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from sv.table import CellWidths, format_table

T = TypeVar("T")


@dataclass(frozen=True)
class TtyUiApproach:
    """Decision record for the terminal UI implementation approach."""

    name: str
    runtime_dependencies: tuple[str, ...]
    alternatives_considered: tuple[str, ...]
    rationale: tuple[str, ...]


CHOSEN_TTY_UI_APPROACH = TtyUiApproach(
    name="stdlib-inline",
    runtime_dependencies=(),
    alternatives_considered=("prompt-toolkit", "rich"),
    rationale=(
        "The existing stdlib selector and table primitives cover the first "
        "target without adding install weight.",
        "prompt-toolkit would improve cross-platform interactive input, but is "
        "not needed until tests expose a first-target gap.",
        "Rich-style rendering is broader than the current plain table and "
        "inline picker needs.",
    ),
)


@dataclass(frozen=True)
class PlainOutput:
    """Non-TTY output primitives that do not perform interactive rendering."""

    def table(
        self,
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        *,
        max_widths: CellWidths | None = None,
        min_widths: CellWidths | None = None,
        max_table_width: int | None = None,
    ) -> str:
        return format_table(
            headers,
            rows,
            max_widths=max_widths,
            min_widths=min_widths,
            max_table_width=max_table_width,
        )


@dataclass(frozen=True)
class TtyUi:
    """TTY-only interactive primitives."""

    selector: Callable[..., list[Any]] | None = None
    table_browser: Callable[..., Sequence[str] | None] | None = None

    def select_many(
        self,
        items: Sequence[T],
        *,
        item_label: Callable[[T], str] = str,
        **kwargs: Any,
    ) -> list[T]:
        if self.selector is None:
            from sv.selector import select_skills

            selector = select_skills
        else:
            selector = self.selector
        return cast("list[T]", selector(items, item_label=item_label, **kwargs))

    def browse_table(
        self,
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        **kwargs: Any,
    ) -> Sequence[str] | None:
        if self.table_browser is None:
            from sv.table import browse_table

            browser = browse_table
        else:
            browser = self.table_browser
        return browser(headers, rows, **kwargs)


def format_plain_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    max_widths: Mapping[str | int, int] | None = None,
    min_widths: Mapping[str | int, int] | None = None,
    max_table_width: int | None = None,
) -> str:
    """Format scriptable/plain output without invoking TTY primitives."""
    return PlainOutput().table(
        headers,
        rows,
        max_widths=max_widths,
        min_widths=min_widths,
        max_table_width=max_table_width,
    )


def select_tty_items(
    items: Sequence[T],
    *,
    item_label: Callable[[T], str] = str,
    **kwargs: Any,
) -> list[T]:
    """Run the chosen interactive selector for TTY-only flows."""
    return TtyUi().select_many(items, item_label=item_label, **kwargs)


def browse_tty_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    **kwargs: Any,
) -> Sequence[str] | None:
    """Run the chosen interactive table browser for TTY-only flows."""
    return TtyUi().browse_table(headers, rows, **kwargs)
