"""One ordered queue for everything that depends on the connected room (CS-F2, decision D13).

Ops act on the *connected* room, so a single worker calls ``connect_to_room`` whenever the target
room changes (spike S4: replies appear under the right parent after switching). That worker also
runs room-dependent reads (``list_participants``, ``load_internal_documents``) so they can never
interleave with a room switch. If a threaded post fails, the fallback is a top-level post prefixed
with the short id.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar, cast

from countersign.switch.models import RoomInfo
from countersign.switch.ops import SwitchError

log = logging.getLogger("countersign.poster")
T = TypeVar("T")


class PosterOps(Protocol):
    """The slice of OpsClient the poster uses."""

    async def connect_to_room(self, room_id: str) -> RoomInfo: ...
    async def post_message(self, body: str, thread_id: str | None = None) -> str: ...
    async def post_message_http(self, room_id: str, content: str) -> None: ...


@dataclass
class _Job:
    room_id: str
    run: Callable[[], Awaitable[object]]
    future: asyncio.Future[object]


class Poster:
    """Serializes every outgoing message and room-bound read."""

    def __init__(self, ops: PosterOps) -> None:
        self._ops = ops
        self._queue: asyncio.Queue[_Job] = asyncio.Queue()
        self._room: str | None = None

    def invalidate(self) -> None:
        """Forget the connected room (call after a reconnect)."""
        self._room = None

    async def in_room(self, room_id: str, fn: Callable[[], Awaitable[T]]) -> T:
        """Run ``fn`` with ``room_id`` connected, in queue order; returns its result."""
        fut: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(_Job(room_id, fn, fut))
        return cast(T, await fut)

    async def post(
        self, room_id: str, body: str, thread_id: str | None = None, prefix: str | None = None
    ) -> str:
        """Queue a message and wait until it is posted; returns its event id ('' on fallback)."""

        async def send() -> str:
            return await self._ops.post_message(body, thread_id)

        try:
            return await self.in_room(room_id, send)
        except SwitchError:
            if thread_id and prefix:
                await self._ops.post_message_http(room_id, f"{prefix} {body}")
                return ""
            raise

    async def run(self) -> None:
        """Worker loop; run as one task for the life of the process."""
        while True:
            job = await self._queue.get()
            try:
                job.future.set_result(await self._execute(job))
            except Exception as exc:
                log.error("room job failed room=%s error=%s", job.room_id, type(exc).__name__)
                if not job.future.done():
                    job.future.set_exception(exc)

    async def _execute(self, job: _Job) -> object:
        last: SwitchError | None = None
        for attempt in (1, 2):
            try:
                if self._room != job.room_id:
                    await self._ops.connect_to_room(job.room_id)
                    self._room = job.room_id
                return await job.run()
            except SwitchError as exc:
                self._room, last = None, exc
                if attempt == 1 and exc.status in (0, 409):
                    continue
                break
        assert last is not None
        raise last
