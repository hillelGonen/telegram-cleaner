import asyncio

import pytest
from conftest import SCAN, make_chat
from fakes import SELF_ID, FakeGateway, FakePrompter, marked
from rich.console import Console

from tgo.menu import TgoApp
from tgo.models import ChatType
from tgo.repository import Repository
from tgo.settings import Settings
from tgo.telegram import TelethonMapper

CHATS = [
    make_chat(marked(1), title="Kan news", idle_days=2),
    make_chat(marked(2), title="Old stuff", idle_days=800),
    make_chat(marked(3), title="Ynet news", idle_days=1, unread=5000),
    make_chat(marked(6), title="Tech daily", idle_days=3, unread=2000, muted=True),
    make_chat(marked(7), ChatType.GROUP, title="Dead group", idle_days=500),
    make_chat(20, ChatType.USER, title="Dana", idle_days=700),
    make_chat(21, ChatType.USER, title="Avi", idle_days=10),
    make_chat(SELF_ID, ChatType.USER, title="Me", idle_days=900),
]


async def no_sleep(_):
    pass


@pytest.fixture
def gw():
    return FakeGateway(chats=CHATS)


@pytest.fixture
def make_app(tmp_path, gw):
    settings = Settings(_env_file=None, tg_api_id=1, tg_api_hash="x",
                        **{"TGO_DATA_DIR": tmp_path, "TGO_BIDI": False})
    settings.ensure_data_dir()
    with Repository(settings.db_path) as repo:
        repo.save_chats(CHATS)
        repo.replace_folders(asyncio.run(gw.folders()))
        repo.last_scan = SCAN
        repo.self_id = SELF_ID
        repo.commit()

    apps = []

    def make(*answers):
        app = TgoApp(settings, FakePrompter(*answers), Console(record=True, width=140),
                     gateway_factory=lambda: gw, sleep=no_sleep)
        apps.append(app)
        return app
    yield make
    for app in apps:
        app.close()


def chats_in_db(app) -> dict[int, object]:
    with app.repo() as repo:
        return {c.id: c for c in repo.chats()}


def include_ids(flt) -> list[int]:
    return [TelethonMapper.peer_ids([p], SELF_ID)[0] for p in flt.include_peers]


def test_clean_old_leave_all(make_app, gw):
    app = make_app(["Private chats", "Groups"], "1 year", "Delete", "All", True)
    app.clean_old()
    assert set(gw.calls) == {("leave", 20), ("leave", marked(7))}  # "Me" protected
    db = chats_in_db(app)
    assert 20 not in db and marked(7) not in db and SELF_ID in db
    assert "can't be undone" in app.console.export_text()


def test_clean_old_untick_some(make_app, gw):
    app = make_app(["Private chats", "Groups"], "1 year", "Delete", "untick", ["Dana"], True)
    app.clean_old()
    assert gw.calls == [("leave", 20)]


def test_clean_old_cancel_does_nothing(make_app, gw):
    make_app(["Private chats"], "1 year", "Archive", "All", False).clean_old()
    make_app(["Private chats"], "1 year", "Archive", "Cancel").clean_old()
    make_app(None).clean_old()
    assert gw.calls == []


def test_clean_any_age_needs_extra_confirm(make_app, gw):
    make_app(["Private chats"], "Any age", False).clean_old()
    assert gw.calls == []
    make_app(["Private chats"], "Any age", True, "Archive", "All", True).clean_old()
    assert [(c[0], set(c[1])) for c in gw.calls] == [("archive_many", {20, 21})]


def test_not_reading_mutes_unmuted_only(make_app, gw):
    app = make_app("1,000", "Mute", "All", True)
    app.not_reading()
    assert gw.calls == [("mute", marked(3))]  # Tech daily already muted
    assert chats_in_db(app)[marked(3)].muted


def test_sort_into_existing_folder(make_app, gw):
    app = make_app("Here", "News", "news daily", "All", True)
    app.sort_folders()
    news = gw.filters[1]
    # Kan news already in; Tech daily + Ynet news added (by title); flags/pins untouched
    assert include_ids(news) == [marked(1), marked(2), marked(6), marked(3)]
    assert news.broadcasts and news.emoticon == "📰"
    assert "News" in [f.title for f in app.repo().folders()]  # local snapshot refreshed


def test_sort_into_new_folder(make_app, gw):
    make_app("Here", "New folder", "Tech", "tech", "All", True).sort_folders()
    tech = gw.filters[-1]
    assert tech.title.text == "Tech" and include_ids(tech) == [marked(6)]


def test_sort_rejects_long_name_and_no_match(make_app, gw):
    app = make_app("Here", "New folder", "A very long folder name")
    app.sort_folders()
    make_app("Here", "News", "zzz").sort_folders()
    assert gw.calls == []
    assert "Too long" in app.console.export_text()


def test_undo_folder_change(make_app, gw):
    before = [bytes(f) for f in gw.filters]
    make_app("Here", "New folder", "Tech", "tech", "All", True).sort_folders()
    assert len(gw.filters) == 4
    make_app("Undo last folder change", True).undo()
    assert [bytes(f) for f in gw.filters] == before


def test_undo_archive_batch(make_app, gw):
    make_app(["Private chats"], "1 year", "Archive", "All", True).clean_old()
    gw.calls.clear()
    app = make_app("Undo last archive", True)
    app.undo()
    assert gw.calls == [("unarchive_many", (20,))]
    assert not chats_in_db(app)[20].archived


def test_undo_shows_left_chats(make_app, gw):
    make_app(["Groups"], "1 year", "Delete", "All", True).clean_old()
    app = make_app("Show chats I left")
    app.undo()
    assert "Dead group" in app.console.export_text()


def test_overview(make_app):
    app = make_app()
    app.overview()
    out = app.console.export_text()
    assert "Dead" in out and "Folders (2 of 10 used)" in out


def test_not_reading_leave_includes_muted(make_app, gw):
    app = make_app("1,000", "Leave", "All", True)
    app.not_reading()
    assert set(gw.calls) == {("leave", marked(3)), ("leave", marked(6))}  # active but unread
    assert "can't be undone" in app.console.export_text()


def test_clean_old_all_types(make_app, gw):
    make_app(["All types"], "1 year", "Archive", "All", True).clean_old()
    # old channel, group and private chat; Me protected; active ones not old
    assert [set(c[1]) for c in gw.calls] == [{marked(2), marked(7), 20}]


def test_one_connection_for_the_whole_session(make_app, gw):
    app = make_app(["Groups"], "1 year", "Mute", "All", True,
                   ["Private chats"], "1 year", "Mute", "All", True)
    app.clean_old()
    asyncio.run(asyncio.sleep(0))  # the prompt library runs its own loop between screens
    app.clean_old()
    assert gw.connects == 1 and len(gw.calls) == 2
    app.close()
    assert gw.connects == 0


def test_sort_in_browser_end_to_end(make_app, gw, monkeypatch):
    """Real HTTP from a 'browser' thread; the menu answers on the main thread."""
    import json
    import threading
    import urllib.request

    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    app = make_app("In your browser")
    results = {}

    def browser():
        import time
        while not opened:
            time.sleep(0.01)
        url = opened[0]
        port, token = url.split(":")[2].split("/")[0], url.split("t=")[1]

        def post(route, body=None):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/{route}",
                                         data=json.dumps(body or {}).encode(),
                                         headers={"X-Tgo-Token": token}, method="POST")
            return json.load(urllib.request.urlopen(req, timeout=10))

        results["state"] = post("state")
        results["save"] = post("save", {"moves": {str(marked(3)): "News", str(marked(6)): "Tech"}})
        post("done")

    t = threading.Thread(target=browser)
    t.start()
    app.sort_folders()
    t.join()

    assert "Ynet news" in [c["title"] for c in results["state"]["chats"].values()]
    assert results["save"]["ok"], results["save"]
    assert include_ids(gw.filters[1])[-1] == marked(3)
    assert gw.filters[-1].title.text == "Tech"
    names = [f["name"] for f in results["save"]["state"]["folders"]]
    assert "Tech" in names  # board reloaded from the refreshed local folders


def test_delete_folders_from_terminal(make_app, gw):
    make_app("Delete folders", ["News"], False).sort_folders()
    assert gw.calls == []  # said no
    app = make_app("Delete folders", ["News"], True)
    app.sort_folders()
    assert gw.calls == [("delete", 2)]
    assert "News" not in [f.title for f in app.repo().folders()]
    make_app("Undo last folder change", True).undo()
    assert gw.filters[1].title.text == "News"


def test_rescans_after_changes(make_app, gw):
    """After a change the local data is re-read from Telegram, not patched by hand."""
    app = make_app(["Groups"], "1 year", "Delete", "All", True)
    app.clean_old()
    assert gw.scans == 1
    assert marked(7) not in chats_in_db(app)  # gone because Telegram no longer has it

    make_app("1,000", "Mute", "All", True).not_reading()
    assert gw.scans == 2

    make_app("Here", "New folder", "Tech", "tech", "All", True).sort_folders()
    assert gw.scans == 3
    assert "Tech" in [f.title for f in app.repo().folders()]

    make_app(["Private chats"], "1 year", "Archive", "All", False).clean_old()  # said no
    assert gw.scans == 3
