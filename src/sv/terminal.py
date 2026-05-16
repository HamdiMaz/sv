from __future__ import annotations

import unicodedata


def escape_terminal_controls(value: object) -> str:
    """Render terminal-active or visually confusing controls as literals."""
    escaped: list[str] = []
    for char in str(value):
        codepoint = ord(char)
        if codepoint < 0x20 or 0x7F <= codepoint < 0xA0:
            escaped.append(f"\\x{codepoint:02x}")
        elif unicodedata.category(char) == "Cf":
            if codepoint <= 0xFFFF:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(f"\\U{codepoint:08x}")
        else:
            escaped.append(char)
    return "".join(escaped)
