import asyncio
import stat
from datetime import UTC, datetime, timedelta

from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.tl.functions.messages import DeleteChatUserRequest, DeleteHistoryRequest
from telethon.tl.types import (
    DialogFilter,
    DialogFilterChatlist,
    DialogFilterDefault,
    InputPeerChannel,
    InputPeerChat,
    InputPeerSelf,
    InputPeerUser,
    JsonArray,
    JsonBool,
    JsonNumber,
    JsonObject,
    JsonObjectValue,
    JsonString,
    PeerNotifySettings,
    TextWithEntities,
)
from telethon.tl.types.messages import AffectedHistory

from tgo.models import FolderKind
from tgo.settings import Settings
from tgo.telegram import TelegramGateway
from tgo.telegram import TelethonMapper as M

CHANNEL_ID = -1000000000123  # marked id of channel 123


def test_folder_from_dialog_filter():
    f = DialogFilter(
        id=2, title=TextWithEntities(text="חדשות", entities=[]), pinned_peers=[],
        include_peers=[InputPeerChannel(123, 0), InputPeerSelf()],
        exclude_peers=[InputPeerUser(55, 0)],
        broadcasts=True, exclude_muted=True, emoticon="📰",
    )
    folder = M.folder(f, 0, self_id=999)
    assert folder.title == "חדשות"
    assert folder.kind == FolderKind.FILTER
    assert folder.include_peers == [CHANNEL_ID, 999]
    assert folder.exclude_peers == [55]
    assert folder.flags.active() == ["broadcasts", "exclude_muted"]


def test_folder_chatlist_and_default():
    cl = DialogFilterChatlist(id=3, title=TextWithEntities(text="Shared", entities=[]),
                              pinned_peers=[], include_peers=[InputPeerChannel(123, 0)])
    assert M.folder(cl, 1, 999).kind == FolderKind.CHATLIST
    assert M.folder(DialogFilterDefault(), 0, 999) is None


def test_is_muted():
    now = datetime.now(UTC)
    assert M.is_muted(PeerNotifySettings(mute_until=now + timedelta(days=1)), now)
    assert not M.is_muted(PeerNotifySettings(mute_until=now - timedelta(days=1)), now)
    assert not M.is_muted(PeerNotifySettings(), now)


def test_json_value_and_limits():
    cfg = JsonObject([
        JsonObjectValue("dialog_filters_limit_default", JsonNumber(10.0)),
        JsonObjectValue("dialog_filters_limit_premium", JsonNumber(30.0)),
        JsonObjectValue("dialog_filters_chats_limit_premium", JsonNumber(200.0)),
        JsonObjectValue("x", JsonArray([JsonString("a"), JsonBool(True)])),
    ])
    data = M.json_value(cfg)
    assert data["x"] == ["a", True]

    free = M.folder_limits(data, premium=False)
    assert (free.folders, free.chats_per_folder) == (10, 100)  # missing key -> default
    prem = M.folder_limits(data, premium=True)
    assert (prem.folders, prem.chats_per_folder) == (30, 200)


# gateway pieces that don't need a network


class FakeClient:
    """Answers DeleteHistory in chunks, like Telegram does for long chats."""

    def __init__(self, chunks: int):
        self.chunks = chunks
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, DeleteHistoryRequest):
            self.chunks -= 1
            return AffectedHistory(pts=1, pts_count=100, offset=100 if self.chunks else 0)

    async def get_input_entity(self, peer_id):
        return {1: InputPeerUser(1, 0), 2: InputPeerChannel(123, 0), 3: InputPeerChat(7)}[peer_id]


def gateway(client) -> TelegramGateway:
    gw = TelegramGateway.__new__(TelegramGateway)
    gw._client = client
    return gw


def test_private_chat_history_deleted_until_done():
    client = FakeClient(chunks=3)
    asyncio.run(gateway(client).leave_chat(1))
    assert [type(r) for r in client.requests] == [DeleteHistoryRequest] * 3
    assert all(r.revoke is False for r in client.requests)  # only for you


def test_leave_channel_and_old_group():
    client = FakeClient(chunks=1)
    asyncio.run(gateway(client).leave_chat(2))
    assert [type(r) for r in client.requests] == [LeaveChannelRequest]

    client = FakeClient(chunks=1)
    asyncio.run(gateway(client).leave_chat(3))
    assert [type(r) for r in client.requests] == [DeleteChatUserRequest, DeleteHistoryRequest]


def test_data_dir_is_owner_only(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "tgo.session").write_text("x")
    (data / "backups").mkdir()
    (data / "backups" / "b.json").write_text("{}")
    Settings(_env_file=None, **{"TGO_DATA_DIR": data}).ensure_data_dir()
    for path, mode in ((data, 0o700), (data / "tgo.session", 0o600),
                       (data / "backups", 0o700), (data / "backups" / "b.json", 0o600)):
        assert stat.S_IMODE(path.stat().st_mode) == mode, path
