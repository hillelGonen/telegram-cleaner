import re

# Pure-Python algorithm: unlike the Rust `bidi.get_display`, it mirrors brackets "(" <-> ")".
from bidi.algorithm import get_display
from rich.markup import escape

# Left-to-right mark: invisible, zero width. Around RTL text it makes terminals that do their
# own bidi keep the line left to right: before it, the line's direction stays LTR; after it,
# following spaces / table borders / numbers don't get pulled into the RTL run.
LRM = "\u200e"

# Hebrew, Arabic, Syriac, Thaana, NKo, Samaritan... + presentation forms
RTL_CHARS = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")


class TerminalText:
    """Prepares user text (chat/folder titles) with Hebrew/Arabic for the terminal.
    bidi=True: reorder it ourselves (terminal can't). False: the terminal reorders it."""

    def __init__(self, bidi: bool = True):
        self.bidi = bidi

    def plain(self, text: str | None) -> str:
        if not text:
            return ""
        if not RTL_CHARS.search(text):
            return text
        return LRM + (get_display(text) if self.bidi else text) + LRM

    def __call__(self, text: str | None) -> str:
        """Bidi-reordered and escaped for Rich markup."""
        return escape(self.plain(text))
