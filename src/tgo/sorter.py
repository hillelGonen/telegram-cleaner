"""Drag-and-drop folder sorting in the browser.

The page talks to a tiny HTTP server on 127.0.0.1. The server thread only passes requests to
the main thread through a queue; the main thread (the menu) owns the database and the Telegram
connection and answers. A random token in the URL keeps other websites from calling the API.
"""
import json
import queue
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from tgo.models import Chat, ChatType, Folder, FolderKind, FolderLimits

SORTABLE = (ChatType.CHANNEL, ChatType.GROUP, ChatType.BOT)
FOLDER_NAME_MAX = 12  # Telegram limit
REPLY_TIMEOUT = 600  # seconds the browser waits for the terminal (a big save can take a while)
# Same port each run keeps the page's address stable, so the browser remembers your settings
# (theme, minimized columns, help seen). Any free port is used if this one is taken.
DEFAULT_PORT = 47391


class SaveRequest(BaseModel):
    """chat id -> folder name, or None for "not in a folder"."""

    moves: dict[int, str | None] = Field(default_factory=dict)
    delete: list[str] = Field(default_factory=list)  # folder names to delete

    def problems(self) -> list[str]:
        return [f"Folder name '{n}' is longer than {FOLDER_NAME_MAX} characters."
                for n in {t for t in self.moves.values() if t} if len(n) > FOLDER_NAME_MAX]


class SorterBoard:
    """What the page shows: own folders as columns, each chat in one column. Pure."""

    def __init__(self, chats: list[Chat], folders: list[Folder], limits: FolderLimits):
        self.chats = [c for c in chats if c.type in SORTABLE]
        self.folders = folders
        self.limits = limits

    def state(self) -> dict[str, Any]:
        own = [f for f in self.folders if f.kind == FolderKind.FILTER]
        placed: set[int] = set()
        columns = []
        known = {c.id for c in self.chats}
        for f in own:
            ids = [cid for cid in [*f.pinned_peers, *f.include_peers]
                   if cid in known and cid not in placed]
            placed.update(ids)
            columns.append({"name": f.title, "emoticon": f.emoticon, "chats": ids,
                            "typeRules": f.flags.has_type_rules})
        # most unread first: the channels you're most behind on are the ones to sort
        loose = [c.id for c in sorted(self.chats, key=lambda c: -c.unread) if c.id not in placed]
        return {
            "folders": columns,
            "loose": loose,
            "chats": {str(c.id): {"title": c.title, "username": c.username, "type": c.type.value,
                                  "unread": c.unread, "members": c.members, "muted": c.muted}
                      for c in self.chats},
            "otherFolders": [f.title for f in self.folders if f.kind != FolderKind.FILTER],
            "limits": {"folders": self.limits.folders, "perFolder": self.limits.chats_per_folder},
            "folderCount": len(self.folders),
            "nameMax": FOLDER_NAME_MAX,
        }


class SorterServer:
    """HTTP server on 127.0.0.1 (random port). Requests go to the main thread via `requests`."""

    def __init__(self, port: int = DEFAULT_PORT):  # 0 = any free port
        self.token = secrets.token_urlsafe(18)
        self.requests: queue.Queue = queue.Queue()
        self._page = files("tgo").joinpath("sorter.html").read_text(encoding="utf-8")
        try:
            self._httpd = ThreadingHTTPServer(("127.0.0.1", port), self._handler_class())
        except OSError:  # taken (e.g. another tgo is open)
            self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class())
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.token}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        if self._thread.is_alive():  # shutdown() would wait forever on a server never started
            self._httpd.shutdown()
        self._httpd.server_close()

    def next_request(self, timeout: float = 0.5):
        """(route, data, reply_queue) or None. Call from the main thread."""
        try:
            return self.requests.get(timeout=timeout)
        except queue.Empty:
            return None

    def _handler_class(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep the terminal clean
                pass

            def _send(self, status: int, body: bytes, ctype: str = "application/json") -> None:
                self.send_response(status)
                self.send_header("Content-Type", f"{ctype}; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy",
                                 "default-src 'self'; script-src 'unsafe-inline'; "
                                 "style-src 'unsafe-inline'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)

            def _host_ok(self) -> bool:  # blocks DNS-rebinding tricks
                return self.headers.get("Host") == f"127.0.0.1:{server.port}"

            def do_GET(self):
                if not self._host_ok():
                    return self._send(HTTPStatus.FORBIDDEN, b"{}")
                if self.path == f"/?t={server.token}":
                    return self._send(HTTPStatus.OK, server._page.encode(), "text/html")
                return self._send(HTTPStatus.NOT_FOUND, b"{}")

            def do_POST(self):
                route = self.path.removeprefix("/api/")
                if (not self._host_ok() or self.headers.get("X-Tgo-Token") != server.token
                        or route not in ("state", "save", "done")):
                    return self._send(HTTPStatus.FORBIDDEN, b'{"error": "forbidden"}')
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    data = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    return self._send(HTTPStatus.BAD_REQUEST, b'{"error": "bad json"}')
                reply: queue.Queue = queue.Queue()
                server.requests.put((route, data, reply))
                try:
                    answer = reply.get(timeout=REPLY_TIMEOUT)
                except queue.Empty:
                    answer = {"ok": False, "problems": ["The terminal didn't answer. Is tgo still running?"]}
                return self._send(HTTPStatus.OK, json.dumps(answer, ensure_ascii=False).encode())

        return Handler


def parse_save(data: dict) -> tuple[SaveRequest, list[str]]:
    try:
        request = SaveRequest.model_validate(data)
    except ValidationError as e:
        return SaveRequest(), [f"Bad request: {e.errors()[0]['msg']}"]
    return request, request.problems()
