from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

import questionary

from tgo.text import TerminalText

T = TypeVar("T")


@dataclass
class Option(Generic[T]):
    label: str
    value: T
    checked: bool = False


class Prompter(Protocol):
    """User questions. Every method returns None when the user cancels (Ctrl+C / Esc)."""

    def select(self, message: str, options: list[Option[T]]) -> T | None: ...
    def checkbox(self, message: str, options: list[Option[T]]) -> list[T] | None: ...
    def text(self, message: str, default: str = "") -> str | None: ...
    def confirm(self, message: str, default: bool = False) -> bool | None: ...


class QuestionaryPrompter:
    """Arrow-key menus in the terminal. Labels are bidi-fixed for Hebrew/Arabic."""

    def __init__(self, text: TerminalText):
        self.t = text

    def _choices(self, options: list[Option[Any]]) -> list[questionary.Choice]:
        return [questionary.Choice(self.t.plain(o.label), value=i, checked=o.checked)
                for i, o in enumerate(options)]

    def select(self, message, options):
        i = questionary.select(message, choices=self._choices(options)).ask()
        return None if i is None else options[i].value

    def checkbox(self, message, options):
        picked = questionary.checkbox(
            message, choices=self._choices(options),
            instruction="(space = tick/untick, a = all, i = invert, enter = done)",
        ).ask()
        return None if picked is None else [options[i].value for i in picked]

    def text(self, message, default=""):
        return questionary.text(message, default=default).ask()

    def confirm(self, message, default=False):
        return questionary.confirm(message, default=default).ask()
