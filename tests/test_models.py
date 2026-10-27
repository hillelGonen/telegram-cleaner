from conftest import make_chat, make_folder

from tgo.folder_index import FolderIndex
from tgo.models import ChatType


def test_folder_shows_by_flags_and_peers():
    all_channels = make_folder(1, "Ch", exclude=[2], broadcasts=True, exclude_muted=True)
    assert all_channels.shows(make_chat(1))
    assert not all_channels.shows(make_chat(2))                  # excluded
    assert not all_channels.shows(make_chat(3, muted=True))      # exclude_muted
    assert not all_channels.shows(make_chat(4, ChatType.GROUP))


def test_folder_shows_listed_and_contacts():
    assert make_folder(1, "Pick", include=[4]).shows(make_chat(4, ChatType.GROUP))
    assert make_folder(1, "Pin", pinned=[4]).shows(make_chat(4, ChatType.GROUP))
    contacts = make_folder(1, "C", contacts=True)
    assert contacts.shows(make_chat(5, ChatType.USER, contact=True))
    assert not contacts.shows(make_chat(6, ChatType.USER))


def test_exclude_read_hides_read_chats():
    f = make_folder(1, "Unread", broadcasts=True, exclude_read=True)
    assert f.shows(make_chat(1, unread=3))
    assert not f.shows(make_chat(2, unread=0))


def test_folder_index():
    chats = [make_chat(1), make_chat(2, ChatType.GROUP)]
    news, picked = make_folder(1, "News", broadcasts=True), make_folder(2, "Pick", include=[1])
    idx = FolderIndex([news, picked], chats)
    assert [f.title for f in idx.showing(chats[0])] == ["News", "Pick"]
    assert idx.in_no_folder(chats[1])
    assert [f.title for f in idx.listing(chats[0])] == ["Pick"]  # flags don't count as listed
    assert idx.shown_count(news) == 1
