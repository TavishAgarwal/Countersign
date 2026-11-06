from __future__ import annotations

import asyncio

import pytest

from countersign.switch.models import RoomInfo
from countersign.switch.ops import SwitchError
from countersign.switch.poster import Poster


class FakeOps:
    def __init__(self) -> None:
        self.log: list[tuple[str, ...]] = []
        self.fail_posts: list[SwitchError] = []

    async def connect_to_room(self, room_id: str) -> RoomInfo:
        self.log.append(("connect", room_id))
        return RoomInfo(room_id=room_id)

    async def post_message(self, body: str, thread_id: str | None = None) -> str:
        if self.fail_posts:
            raise self.fail_posts.pop(0)
        self.log.append(("post", body, thread_id or "-"))
        return f"ev{len(self.log)}"

    async def post_message_http(self, room_id: str, content: str) -> None:
        self.log.append(("http", room_id, content))


async def run(ops: FakeOps, coro_factory):  # type: ignore[no-untyped-def]
    poster = Poster(ops)
    worker = asyncio.create_task(poster.run())
    try:
        return await coro_factory(poster)
    finally:
        worker.cancel()


async def test_posts_in_order_and_switches_rooms_only_when_needed() -> None:
    ops = FakeOps()

    async def go(p: Poster) -> None:
        await asyncio.gather(
            p.post("A", "1"), p.post("A", "2", "t1"), p.post("B", "3"), p.post("A", "4")
        )

    await run(ops, go)
    assert ops.log == [
        ("connect", "A"), ("post", "1", "-"), ("post", "2", "t1"),
        ("connect", "B"), ("post", "3", "-"), ("connect", "A"), ("post", "4", "-"),
    ]  # fmt: skip


async def test_retries_once_after_connection_not_open_then_succeeds() -> None:
    ops = FakeOps()
    ops.fail_posts = [SwitchError(409, "not open")]
    out = await run(ops, lambda p: p.post("A", "hello"))
    assert out.startswith("ev") and ops.log.count(("connect", "A")) == 2


async def test_thread_post_falls_back_to_prefixed_top_level() -> None:
    ops = FakeOps()
    ops.fail_posts = [SwitchError(500, "x"), SwitchError(500, "x")]
    out = await run(ops, lambda p: p.post("A", "paid", "root1", prefix="CS-7f3a2c"))
    assert out == "" and ops.log[-1] == ("http", "A", "CS-7f3a2c paid")


async def test_failure_without_fallback_raises_but_the_queue_keeps_working() -> None:
    ops = FakeOps()
    ops.fail_posts = [SwitchError(500, "x")]  # a 500 is not retried

    async def go(p: Poster) -> str:
        with pytest.raises(SwitchError):
            await p.post("A", "lost")
        return await p.post("A", "next")

    assert (await run(ops, go)).startswith("ev")


async def test_invalidate_forces_a_reconnect_to_the_room() -> None:
    ops = FakeOps()

    async def go(p: Poster) -> None:
        await p.post("A", "1")
        p.invalidate()
        await p.post("A", "2")

    await run(ops, go)
    assert ops.log.count(("connect", "A")) == 2
