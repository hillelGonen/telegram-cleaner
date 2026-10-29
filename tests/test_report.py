import pytest
from conftest import make_chat, make_folder
from pydantic import ValidationError

from tgo.models import ChatType
from tgo.report import CleanupReporter, ReportOptions
from tgo.text import TerminalText


@pytest.fixture
def report(scan_time):
    chats = [
        make_chat(1, title="News", unread=1500, muted=True),
        make_chat(2, title="Old channel", idle_days=400),
        make_chat(3, ChatType.GROUP, title="Loose group", idle_days=5, unread=2000),
        make_chat(4, title="Never posted", idle_days=None),
        make_chat(5, ChatType.USER, title="Dana", idle_days=400, unread=5000),
    ]
    folders = [make_folder(1, "A", include=[1, 2])]
    return CleanupReporter(chats, folders, scan_time).build()


def test_dead_channels_and_groups_only(report):
    assert [c.title for c in report.dead] == ["Old channel", "Never posted"]  # Dana is a user


def test_unread_pileup_sorted(report):
    assert [c.title for c in report.unread_pileup] == ["Loose group", "News"]


def test_unfoldered(report):
    assert [c.title for c in report.unfoldered] == ["Loose group", "Never posted"]


def test_options(scan_time):
    r = CleanupReporter([make_chat(1, idle_days=100)], [], scan_time,
                        ReportOptions(dead_days=90)).build()
    assert len(r.dead) == 1
    with pytest.raises(ValidationError):
        ReportOptions(dead_days=0)


def test_terminal_text_bidi():
    lrm = "\u200e"
    assert TerminalText(bidi=True).plain("חדשות 12") == lrm + "12 תושדח" + lrm  # we reorder
    assert TerminalText(bidi=False).plain("חדשות 12") == lrm + "חדשות 12" + lrm  # terminal does
    assert TerminalText(bidi=True).plain("חדשות (12)") == lrm + "(12) תושדח" + lrm  # mirrored
    assert TerminalText(bidi=True).plain("News") == "News"  # no mark on plain text
    assert TerminalText()("[x]") == r"\[x]"  # rich markup escaped


def test_rtl_mark_keeps_table_width():
    from rich.cells import cell_len
    assert cell_len(TerminalText(bidi=False).plain("חדשות")) == 5
