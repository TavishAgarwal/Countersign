from __future__ import annotations

import json

import httpx
import pytest

from countersign.switch import media
from countersign.switch.ops import OpsClient, SwitchError
from countersign.switch.poll import poll_events, to_event

CONTEXT = {"result": {"threads": [
    {"root": {"id": "m1", "kind": "message", "sender": "@s:l", "sender_name": "riya",
              "body": "@intake process this", "timestamp": 100,
              "attachments": [{"filename": "a.pdf", "mxc": "switch-media://x"}]},
     "replies": [{"id": "m2", "kind": "message", "sender": "@a:l", "sender_name": "intake",
                  "body": "@countersign propose", "timestamp": 200, "attachments": []}]},
    {"root": {"id": "m3", "kind": "room_join", "sender": "@s:l", "sender_name": "x",
              "body": "x joined", "timestamp": 50, "attachments": []}, "replies": []},
], "truncated": False}}  # fmt: skip


def client(handler) -> OpsClient:  # type: ignore[no-untyped-def]
    async def nosleep(_: float) -> None:
        return None

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpsClient("http://h:1", "agent-1", "KEY", lambda: "conn-1", http, sleep=nosleep)


async def test_call_sends_auth_and_connection_headers_and_unwraps_result() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"result": {"event_id": "e1"}})

    c = client(handler)
    assert await c.post_message("hi", "t9") == "e1"
    r = seen[0]
    assert r.url.path == "/agents/agent-1/ops/post_message"
    assert (
        r.headers["authorization"] == "Bearer KEY"
        and r.headers["x-switch-connection-id"] == "conn-1"
    )
    assert json.loads(r.content) == {"body": "hi", "thread_id": "t9"}


async def test_reads_retry_on_409_and_5xx_but_not_on_4xx() -> None:
    calls = {"n": 0}

    def flaky(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return (
            httpx.Response(409, text="not open")
            if calls["n"] < 3
            else httpx.Response(200, json={"result": []})
        )

    assert await client(flaky).list_participants() == ()
    assert calls["n"] == 3

    def forbidden(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403, text="no")

    calls["n"] = 0
    with pytest.raises(SwitchError) as exc:
        await client(forbidden).list_participants()
    assert exc.value.status == 403 and calls["n"] == 1


async def test_post_is_never_retried_after_a_read_timeout() -> None:
    calls = {"n": 0}

    def slow(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("slow")

    with pytest.raises(SwitchError):
        await client(slow).post_message("once")
    assert calls["n"] == 1


async def test_read_context_flattens_threads() -> None:
    c = client(lambda req: httpx.Response(200, json=CONTEXT))
    msgs = await c.read_context("R1")
    assert [(m["id"], m["thread_id"]) for m in msgs] == [("m1", None), ("m2", "m1"), ("m3", None)]


async def test_addressing_rules_and_documents_parse() -> None:
    detail = {"result": {"addressing_policy": {"rules": [
        {"rooms": ["R"], "room_groups": "*", "users": ["u1"], "agents": [], "owner": False,
         "owner_agents": False, "platform": False, "future_field": 1}]}}}  # fmt: skip
    c = client(lambda req: httpx.Response(200, json=detail))
    rules = await c.addressing_rules()
    assert rules[0].users == ("u1",) and rules[0].agents == ()
    docs = {"result": [{"id": "d1", "content": "text", "name": "policy.yaml"}]}
    assert await client(lambda req: httpx.Response(200, json=docs)).load_documents(["d1"]) == {
        "d1": "text"
    }


async def test_connect_to_room_reads_participants_and_documents() -> None:
    payload = {"result": {"name": "ap-approvals", "participants": [
        {"id": "u1", "name": "meera", "type": "user"}, {"id": "a1", "name": "countersign", "type": "agent"}],
        "documents": [{"id": "d1", "name": "policy.yaml"}]}}  # fmt: skip
    room = await client(lambda req: httpx.Response(200, json=payload)).connect_to_room("R1")
    assert room.room_id == "R1" and [p.name for p in room.participants] == ["meera", "countersign"]
    assert room.documents[0].name == "policy.yaml"


async def test_media_download() -> None:
    def ok(req: httpx.Request) -> httpx.Response:
        assert (
            req.url.path == "/agents/a/rooms/R/media"
            and req.url.params["mxc"] == "switch-media://x"
        )
        return httpx.Response(200, content=b"%PDF-1.3 ...")

    http = httpx.AsyncClient(transport=httpx.MockTransport(ok))
    assert (await media.download(http, "http://h", "a", "K", "R", "switch-media://x")).startswith(
        b"%PDF"
    )
    bad = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(media.MediaError):
        await media.download(bad, "http://h", "a", "K", "R", "m")
    big = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=b"x" * (media.MAX_BYTES + 1))
        )
    )
    with pytest.raises(media.MediaError):
        await media.download(big, "http://h", "a", "K", "R", "m")


def test_to_event_maps_read_context_messages() -> None:
    msg = CONTEXT["result"]["threads"][0]["replies"][0] | {"thread_id": "m1"}
    ev = to_event("R1", msg, 7)
    assert ev is not None and ev.sequence == 7 and ev.payload.thread_id == "m1"
    assert ev.payload.sender_name == "intake" and ev.payload.addressed
    assert to_event("R1", CONTEXT["result"]["threads"][1]["root"], 1) is None


class PollOps:
    def __init__(self, rounds: list[list[dict[str, object]]]) -> None:
        self.rounds = rounds

    async def read_context(self, room_id: str, limit: int = 50, since: str | None = None):  # type: ignore[no-untyped-def]
        return self.rounds.pop(0) if self.rounds else []


async def test_poll_yields_new_mentions_once_in_time_order() -> None:
    def m(i: str, ts: int, body: str, who: str = "meera") -> dict[str, object]:
        return {"id": i, "kind": "message", "sender": "@x", "sender_name": who, "body": body,
                "timestamp": ts, "attachments": [], "thread_id": None}  # fmt: skip

    round1 = [m("b", 300, "@countersign approve"), m("a", 200, "@Countersign whoami"),
              m("old", 10, "@countersign help"), m("chat", 250, "no mention"),
              m("self", 260, "@countersign echo", who="countersign")]  # fmt: skip
    ops = PollOps([round1, round1])
    handled: set[str] = set()
    out: list[str] = []

    async def nosleep(_: float) -> None:
        return None

    async for ev in poll_events(
        ops, ["R1"], "countersign", 100, lambda i: i not in handled, sleep=nosleep
    ):
        out.append(ev.payload.message_id)
        handled.add(ev.payload.message_id)
        if len(out) == 2:
            break
    assert out == ["a", "b"]


async def test_date_header_becomes_server_time() -> None:
    import httpx

    from countersign.switch.ops import OpsClient

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"result": []}, headers={"date": "Thu, 05 Nov 2026 17:47:39 GMT"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ops = OpsClient("http://x", "a", "k", lambda: "c", client)
        assert ops.server_time_ms == 0
        await ops.call("list_all_rooms")
        assert ops.server_time_ms == 1793900859000
