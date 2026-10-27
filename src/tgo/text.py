import re

# Pure-Python algorithm: unlike the Rust `bidi.get_display`, it mirrors brackets "(" <-> ")".
from bidi.algorithm import get_display
from rich.markup import escape

# Hebrew, Arabic, Syriac, Thaana, NKo, Samaritan... + presentation forms
RTL_CHARS = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")


class TerminalText:
    """Prepares user text (chat/folder titles) for a terminal that can't do bidi."""

    def __init__(self, bidi: bool = True):
        self.bidi = bidi

    def plain(self, text: str | None) -> str:
        if not text:
            return ""
        if self.bidi and RTL_CHARS.search(text):
            return get_display(text)
        return text

    def __call__(self, text: str | None) -> str:
        """Bidi-reordered and escaped for Rich markup."""
        return escape(self.plain(text))
