from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import threading
from typing import Any, TextIO, TypeVar, cast
import unicodedata

from sv.table import CellWidths, DetailLine, detail_line, format_table

T = TypeVar("T")

__all__ = [
    "CHOSEN_TTY_UI_APPROACH",
    "CellWidths",
    "DetailLine",
    "LoadingReporter",
    "PlainOutput",
    "TtyUi",
    "TtyUiApproach",
    "browse_tty_table",
    "detail_line",
    "format_plain_table",
    "format_table",
    "select_tty_items",
]


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


class LoadingReporter:
    """TTY-only in-place loading reporter for long source operations."""

    def __init__(
        self,
        stream: TextIO,
        *,
        enabled: bool | None = None,
        frames: Sequence[str] = (
            "⠋",
            "⠙",
            "⠹",
            "⠸",
            "⠼",
            "⠴",
            "⠦",
            "⠧",
            "⠇",
            "⠏",
        ),
        interval_seconds: float = 0.1,
    ):
        self._stream = stream
        self._enabled = stream.isatty() if enabled is None else enabled
        self._frames = tuple(frames) or ("-",)
        self._interval_seconds = interval_seconds
        self._lock = threading.RLock()
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._active_count = 0
        self._message = ""
        self._frame_index = 0
        self._last_render_width = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    @contextmanager
    def operation(self, message: str) -> Iterator["LoadingReporter"]:
        if not self.enabled:
            yield self
            return

        self._start(message)
        try:
            yield self
        finally:
            self._stop()

    def print_line(self, message: str) -> None:
        if not self.enabled:
            print(message, file=self._stream)
            return

        with self._lock:
            active = self._active_count > 0
            if active:
                self._clear_locked()
            self._stream.write(f"{message}\n")
            if active:
                self._render_locked()
            self._stream.flush()

    def _start(self, message: str) -> None:
        with self._lock:
            self._active_count += 1
            if self._active_count > 1:
                return
            if self._last_render_width > 0:
                self._clear_locked()
            self._message = message
            stop_event = threading.Event()
            self._stop_event = stop_event
            self._render_locked()
            self._thread = threading.Thread(
                target=self._animate,
                args=(stop_event,),
                name="sv-loading-reporter",
                daemon=True,
            )
            self._thread.start()

    def _stop(self) -> None:
        thread: threading.Thread | None = None
        stop_event: threading.Event | None = None
        with self._lock:
            if self._active_count == 0:
                return
            self._active_count -= 1
            if self._active_count > 0:
                return
            stop_event = self._stop_event
            if stop_event is not None:
                stop_event.set()
            thread = self._thread

        if thread is not None:
            thread.join(timeout=1.0)

        with self._lock:
            if (
                self._thread is not thread
                or self._stop_event is not stop_event
                or self._active_count > 0
            ):
                return
            self._thread = None
            self._stop_event = None
            self._clear_locked()
            self._message = ""

    def _animate(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(self._interval_seconds):
            with self._lock:
                if self._stop_event is not stop_event or self._active_count == 0:
                    return
                self._render_locked()

    def _render_locked(self) -> None:
        frame = self._frames[self._frame_index % len(self._frames)]
        self._frame_index += 1
        rendered = f"{frame} {self._message}"
        self._last_render_width = _display_width(rendered)
        self._stream.write(f"\r{rendered}")
        self._stream.flush()

    def _clear_locked(self) -> None:
        if self._last_render_width == 0:
            return
        self._stream.write("\r" + (" " * self._last_render_width) + "\r")
        self._stream.flush()
        self._last_render_width = 0


def _display_width(value: str) -> int:
    return sum(_character_width(char) for char in value)


def _character_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    if unicodedata.east_asian_width(char) in {"F", "W"}:
        return 2
    return 1


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
