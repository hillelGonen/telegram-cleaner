import pytest
from conftest import make_chat, make_folder
from pydantic import ValidationError

from tgo.models import ChatType, FolderLimits
from tgo.report import CleanupReporter, ReportOptions
from tgo.text import TerminalText


@pytest.fixture
def report(scan_time):
    chats = [
        make_chat(1, title="News", unread=900, muted=True),
        make_chat(2, title="Old channel", idle_days=200),
        make_chat(3, ChatType.GROUP, title="Loose group", idle_days=5),
        make_chat(4, title="Never posted", idle_days=None),
        make_chat(5, ChatType.USER, title="Dana", idle_days=400),
    ]
    folders = [
        make_folder(1, "A", include=[1, 2]),
        make_folder(2, "B", include=[1], pinned=[2]),
    ]
    limits = FolderLimits(folders=10, chats_per_folder=2)
    return CleanupReporter(chats, folders, scan_time, limits).build()


def test_unfoldered_hides_private_by_default(report):
    assert [c.title for c in report.unfoldered] == ["Never posted", "Loose group"]
    assert report.unfoldered_private == 1


def test_unfoldered_with_private(scan_time):
    r = CleanupReporter([make_chat(5, ChatType.USER)], [], scan_time,
                        options=ReportOptions(include_private=True)).build()
    assert len(r.unfoldered) == 1 and r.unfoldered_private == 0


def test_dead_channels_only_oldest_first(report):
    assert [(d.chat.title, d.idle_days) for d in report.dead] == [
        ("Never posted", None), ("Old channel", 200)]


def test_unread_and_multi_folder(report):
    assert [c.title for c in report.unread_pileup] == ["News"]
    assert {m.chat.title: m.folders for m in report.multi_folder} == {
        "News": ["A", "B"], "Old channel": ["A", "B"]}


def test_folder_usage_near_limit(report):
    usage = {u.folder.title: (u.listed, u.near_limit) for u in report.folder_usage}
    assert usage == {"A": (2, True), "B": (2, True)}
    assert report.folder_usage[0].ratio == 1.0


def test_options_validate():
    with pytest.raises(ValidationError):
        ReportOptions(dead_days=0)


def test_terminal_text_bidi():
    assert TerminalText(bidi=True).plain("חדשות 12") == "12 תושדח"
    assert TerminalText(bidi=False).plain("חדשות 12") == "חדשות 12"
    assert TerminalText(bidi=True).plain("חדשות (12)") == "(12) תושדח"  # brackets mirrored
    assert TerminalText(bidi=True).plain("News") == "News"
    assert TerminalText()("[x]") == r"\[x]"  # rich markup escaped
