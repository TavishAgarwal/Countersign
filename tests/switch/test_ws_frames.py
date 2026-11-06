from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from countersign.switch.models import (
    ConnectionState,
    Evicted,
    Gap,
    MessageEvent,
    Other,
    Ping,
    Refused,
    parse_frame,
)
from countersign.switch.ws import TakenOver, WsTransport

LIVE_MESSAGE = {
    "event": "message",
    "data": {
        "type": "message", "room_id": "R1", "bridge_id": "B", "channel_type": "channel_public",
        "payload": {
            "addressed": True, "sender": "@switch-mattermost-b-meera:localhost",
            "sender_name": "meera", "sender_kind": None, "on_behalf_of": None,
            "message_id": "sw_1", "body": "@countersign whoami", "timestamp": 1793825834431,
            "thread_id": None, "attachments": [], "opening_message": None, "sender_role": None,
        },
        "sequence": 4294967297, "missed": {"count": 0, "reason": None},
    },
    "id": 4294967297,
}  # fmt: skip
LIVE_STATE = {
    "event": "connection_state",
    "data": {"connection_id": "c1", "agent_id": "a", "generation": 8589934592, "scope": "all",
             "filter": "addressed", "rooms": ["R1"], "cursor": 4294967296, "protocol": 9},
}  # fmt: skip


def test_parse_live_message_frame() -> None:
    f = parse_frame(json.dumps(LIVE_MESSAGE))
    assert isinstance(f, MessageEvent)
    assert (f.room_id, f.sequence) == ("R1", 4294967297)
    p = f.payload
    assert (p.sender_name, p.sender_kind, p.sender_role, p.thread_id) == ("meera", None, None, None)
    assert p.sender.startswith("@switch-mattermost") and p.message_id == "sw_1"


def test_parse_connection_state_and_control_frames() -> None:
    s = parse_frame(json.dumps(LIVE_STATE))
    assert isinstance(s, ConnectionState) and s.generation == 8589934592 and s.rooms == ("R1",)
    assert isinstance(parse_frame('{"type":"ping"}'), Ping)
    assert isinstance(parse_frame('{"event":"ping","data":{}}'), Ping)
    g = parse_frame('{"event":"gap","data":{"resumed_at":99,"all_rooms":true,"reason":"x"}}')
    assert isinstance(g, Gap) and g.resumed_at == 99 and g.all_rooms
    e = parse_frame('{"event":"evicted","data":{"code":"taken_over","reason":"r"}}')
    assert isinstance(e, Evicted) and e.code == "taken_over"
    r = parse_frame('{"event":"refused","data":{"status":409,"detail":"d"}}')
    assert isinstance(r, Refused) and r.status == 409


@pytest.mark.parametrize(
    "raw",
    ["not json", "[]", "42", '{"event":"message","data":{"room_id":"R"}}', '{"event":"zzz"}', "{}"],
)
def test_malformed_or_unknown_frames_are_ignored_safely(raw: str) -> None:
    assert isinstance(parse_frame(raw), Other)


class FakeWS:
    def __init__(self, frames: list[object]) -> None:
        self.frames, self.sent = frames, []

    def __aiter__(self) -> AsyncIterator[str]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[str]:
        for f in self.frames:
            await asyncio.sleep(0)
            yield f if isinstance(f, str) else json.dumps(f)

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def close(self) -> None:
        return None


class FakeConnector:
    def __init__(self, scripts: list[list[object]]) -> None:
        self.scripts, self.urls, self.sockets = scripts, [], []

    def __call__(self, url: str, headers: dict[str, str]):  # type: ignore[no-untyped-def]
        self.urls.append(url)
        ws = FakeWS(self.scripts[len(self.sockets)])
        self.sockets.append(ws)

        @asynccontextmanager
        async def cm():  # type: ignore[no-untyped-def]
            yield ws

        return cm()


def transport(conn: FakeConnector, **kw: object) -> WsTransport:
    async def no_sleep(_: float) -> None:
        return None

    return WsTransport("http://h:1", "agent", "key", "cid-1", ["R1", "R2"], connect=conn,
                       sleep=no_sleep, **kw)  # type: ignore[arg-type]  # fmt: skip


async def test_pings_are_answered_even_while_the_consumer_is_busy() -> None:
    frames = [LIVE_STATE, {"type": "ping"}, LIVE_MESSAGE, {"type": "ping"}, LIVE_MESSAGE,
              {"event": "evicted", "data": {"code": "taken_over"}}]  # fmt: skip
    conn = FakeConnector([frames])
    t = transport(conn)
    agen = t.events()
    first = await agen.__anext__()
    assert isinstance(first, ConnectionState)
    await asyncio.sleep(0.05)  # consumer does nothing; the reader keeps running
    assert [m for m in conn.sockets[0].sent if m["type"] == "pong"] == [
        {"type": "pong", "cursor": 0}, {"type": "pong", "cursor": 0}
    ]  # fmt: skip
    t.ack(4294967297)
    with pytest.raises(TakenOver):
        async for _ in agen:
            pass


async def test_pong_carries_the_acknowledged_cursor() -> None:
    frames = [LIVE_MESSAGE, {"type": "ping"}, {"event": "evicted", "data": {"code": "taken_over"}}]
    conn = FakeConnector([frames])
    t = transport(conn)
    with pytest.raises(TakenOver):
        async for ev in t.events():
            if isinstance(ev, MessageEvent):
                t.ack(ev.sequence)
    assert conn.sockets[0].sent[-1] == {"type": "pong", "cursor": 4294967297}


async def test_heartbeat_lapse_reconnects_and_resumes_from_the_cursor() -> None:
    script1 = [LIVE_STATE, LIVE_MESSAGE, {"event": "evicted", "data": {"code": "heartbeat_lapsed"}}]
    script2 = [LIVE_MESSAGE | {"id": 5}, {"event": "evicted", "data": {"code": "taken_over"}}]
    conn = FakeConnector([script1, script2])
    t = transport(conn)
    got: list[str] = []
    with pytest.raises(TakenOver):
        async for ev in t.events():
            if isinstance(ev, MessageEvent):
                got.append(ev.payload.message_id)
                t.ack(ev.sequence)
    assert len(conn.urls) == 2 and len(got) == 2
    first, second = conn.urls
    assert "start_from" not in first and "rooms=R1%2CR2" in first and "connection_id=cid-1" in first
    assert "start_from=4294967297" in second and "connection_id=cid-1" in second
    assert "expected_generation=8589934592" in second and "scope=all" in second
    assert "filter=addressed" in second


async def test_network_error_reconnects() -> None:
    class Boom(FakeWS):
        async def _gen(self) -> AsyncIterator[str]:
            raise ConnectionResetError("x")
            yield ""

    conn = FakeConnector([[], [{"event": "evicted", "data": {"code": "taken_over"}}]])
    orig = conn.__call__

    def patched(url: str, headers: dict[str, str]):  # type: ignore[no-untyped-def]
        cm = orig(url, headers)
        if len(conn.urls) == 1:
            conn.sockets[-1] = Boom([])
        return cm

    t = transport(conn)
    t._connect = patched  # type: ignore[assignment]
    with pytest.raises(TakenOver):
        async for _ in t.events():
            pass
    assert len(conn.urls) == 2


async def test_gap_moves_the_cursor_and_is_yielded() -> None:
    conn = FakeConnector([[{"event": "gap", "data": {"resumed_at": 77}},
                           {"event": "evicted", "data": {"code": "taken_over"}}]])  # fmt: skip
    t = transport(conn)
    seen: list[object] = []
    with pytest.raises(TakenOver):
        async for ev in t.events():
            seen.append(ev)
    assert isinstance(seen[0], Gap) and t.cursor == 77


async def test_refused_409_is_final_and_start_from_uses_stored_state() -> None:
    conn = FakeConnector([[{"event": "refused", "data": {"status": 409, "detail": "x"}}]])
    t = transport(conn, cursor=50, generation=12)
    with pytest.raises(TakenOver):
        async for _ in t.events():
            pass
    assert "start_from=50" in conn.urls[0] and "expected_generation=12" in conn.urls[0]
