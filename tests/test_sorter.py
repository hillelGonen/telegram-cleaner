import json
import threading
import urllib.error
import urllib.request

import pytest
from conftest import make_chat, make_folder

from tgo.models import ChatType, FolderKind, FolderLimits
from tgo.sorter import SorterBoard, SorterServer, parse_save

CHATS = [
    make_chat(1, title="Kan news", unread=5),
    make_chat(2, title="Ynet", unread=500),
    make_chat(3, ChatType.GROUP, title="Family", unread=50),
    make_chat(4, ChatType.BOT, title="Helper bot"),
    make_chat(5, ChatType.USER, title="Dana"),  # private chats aren't sorted here
    make_chat(6, title="Shared only"),
]
FOLDERS = [
    make_folder(10, "News", include=[1, 2], pinned=[3], broadcasts=True),
    make_folder(11, "Also news", include=[2]),  # 2 already placed in News
    make_folder(12, "Shared", include=[6]).model_copy(update={"kind": FolderKind.CHATLIST}),
]


def test_board_state():
    s = SorterBoard(CHATS, FOLDERS, FolderLimits()).state()
    assert [(f["name"], f["chats"], f["typeRules"]) for f in s["folders"]] == [
        ("News", [3, 1, 2], True), ("Also news", [], False)]
    assert s["loose"] == [4, 6]  # shared-folder chats count as loose; users excluded
    assert set(s["chats"]) == {"1", "2", "3", "4", "6"}
    assert s["otherFolders"] == ["Shared"] and s["folderCount"] == 3


def test_parse_save():
    request, problems = parse_save({"moves": {"-1001": "News", "2": None}, "delete": ["Old"]})
    assert request.moves == {-1001: "News", 2: None} and request.delete == ["Old"] and problems == []
    _, problems = parse_save({"moves": {"1": "A name that is too long"}})
    assert "longer than" in problems[0]
    _, problems = parse_save({"moves": {"x": "News"}})
    assert problems[0].startswith("Bad request")


@pytest.fixture
def server():
    srv = SorterServer(port=0)
    srv.start()
    yield srv
    srv.stop()


def call(srv, route, body=None, token=None, host=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{srv.port}/api/{route}", data=json.dumps(body or {}).encode(),
        headers={"X-Tgo-Token": token or srv.token, "Content-Type": "application/json",
                 **({"Host": host} if host else {})}, method="POST")
    return urllib.request.urlopen(req, timeout=5)


def answer_one(srv, response):
    def run():
        route, data, reply = srv.next_request(timeout=5)
        reply.put({**response, "route": route, "data": data})
    t = threading.Thread(target=run)
    t.start()
    return t


def test_api_round_trip_through_main_thread(server):
    t = answer_one(server, {"ok": True})
    body = json.load(call(server, "save", {"moves": {"1": "News"}}))
    t.join()
    assert body == {"ok": True, "route": "save", "data": {"moves": {"1": "News"}}}


def test_page_needs_token(server):
    page = urllib.request.urlopen(server.url, timeout=5)
    assert b"Sort into folders" in page.read()
    assert "default-src 'self'" in page.headers["Content-Security-Policy"]
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"http://127.0.0.1:{server.port}/?t=wrong", timeout=5)
    assert e.value.code == 404


@pytest.mark.parametrize("kw", [{"token": "wrong"}, {"host": "evil.example:80"}])
def test_api_rejects_other_sites(server, kw):
    with pytest.raises(urllib.error.HTTPError) as e:
        call(server, "state", **kw)
    assert e.value.code == 403
    assert server.next_request(timeout=0.1) is None  # never reached the main thread


def test_unknown_route_rejected(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        call(server, "delete-everything")
    assert e.value.code == 403


def test_stable_port_with_fallback():
    from tgo.sorter import DEFAULT_PORT
    first = SorterServer()
    second = SorterServer()  # default port now taken -> any free port
    try:
        assert first.port == DEFAULT_PORT and second.port != DEFAULT_PORT
    finally:
        first.stop()
        second.stop()
