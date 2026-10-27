from datetime import datetime, timedelta, timezone

from telethon.tl.types import (
    DialogFilter,
    DialogFilterChatlist,
    DialogFilterDefault,
    InputPeerChannel,
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

from tgo.models import FolderKind
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
    now = datetime.now(timezone.utc)
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
