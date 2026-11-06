"""WebSocket transport: pong-first, resume, gap and evicted handling (CS-F2, architecture §5.8).

Pings are answered by the reader task the moment they arrive, never behind event processing.
Events go through a queue to the single consumer. The pong cursor is the highest sequence the
consumer has acknowledged (``ack``), so a crash resumes without losing an event.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Protocol
from urllib.parse import urlencode

import websockets

from countersign.switch.models import (
    ConnectionState,
    Evicted,
    Frame,
    Gap,
    MessageEvent,
    Ping,
    Refused,
    parse_frame,
)

log = logging.getLogger("countersign.ws")
BACKOFF = (1.0, 2.0, 4.0, 8.0, 15.0)


class TakenOver(RuntimeError):
    """Another connection took over; do not reconnect (the process exits with code 3)."""


class _Socket(Protocol):
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...
    async def send(self, message: str) -> None: ...
    async def close(self) -> None: ...


Connector = Callable[[str, dict[str, str]], "contextlib.AbstractAsyncContextManager[_Socket]"]


def _default_connect(
    url: str, headers: dict[str, str]
) -> contextlib.AbstractAsyncContextManager[_Socket]:
    return websockets.connect(url, additional_headers=headers, ping_interval=None)


class WsTransport:
    """Yields message, gap and connection_state frames; handles everything else itself."""

    def __init__(
        self,
        base_url: str,
        agent_id: str,
        api_key: str,
        connection_id: str,
        rooms: Sequence[str],
        cursor: int = 0,
        generation: int | None = None,
        connect: Connector = _default_connect,
        sleep: Callable[[float], object] = asyncio.sleep,
    ) -> None:
        self.connection_id, self.cursor, self.generation = connection_id, cursor, generation
        self.rooms = tuple(rooms)
        self._ws_base = base_url.rstrip("/").replace("http", "ws", 1)
        self._agent_id, self._key = agent_id, api_key
        self._connect, self._sleep = connect, sleep
        self._socket: _Socket | None = None
        self._open = False

    @property
    def is_open(self) -> bool:
        """True while a websocket is connected (ops need an open connection)."""
        return self._open

    def ack(self, sequence: int) -> None:
        """Record that events up to ``sequence`` are fully processed (the pong cursor)."""
        self.cursor = max(self.cursor, sequence)

    def _url(self) -> str:
        q: dict[str, str] = {
            "connection_id": self.connection_id,
            "scope": "all",
            "filter": "addressed",
        }
        if self.cursor:
            q["start_from"] = str(self.cursor)
        if self.generation is not None:
            q["expected_generation"] = str(self.generation)
        if self.rooms:
            q["rooms"] = ",".join(self.rooms)
        return f"{self._ws_base}/agents/{self._agent_id}/connection/ws?{urlencode(q)}"

    async def _reader(self, ws: _Socket, queue: asyncio.Queue[Frame | None]) -> None:
        try:
            async for raw in ws:
                frame = parse_frame(raw)
                if isinstance(frame, Ping):
                    await ws.send(json.dumps({"type": "pong", "cursor": self.cursor}))
                    continue
                if isinstance(frame, ConnectionState):
                    self.generation = frame.generation
                queue.put_nowait(frame)
        finally:
            queue.put_nowait(None)

    async def events(self) -> AsyncIterator[MessageEvent | Gap | ConnectionState]:
        """Yield frames for the consumer; reconnect and resume on recoverable failures.

        Raises TakenOver on a final eviction and OSError-like errors never escape: they trigger
        a reconnect with back-off (the caller switches to poll mode after 60 s of failures).
        """
        failures = 0
        while True:
            queue: asyncio.Queue[Frame | None] = asyncio.Queue()
            try:
                async with self._connect(
                    self._url(), {"Authorization": f"Bearer {self._key}"}
                ) as ws:
                    self._socket, self._open = ws, True
                    reader = asyncio.create_task(self._reader(ws, queue))
                    try:
                        while (frame := await queue.get()) is not None:
                            failures = 0
                            if isinstance(frame, Evicted):
                                if frame.code == "taken_over":
                                    raise TakenOver(frame.reason)
                                log.warning("evicted code=%s reason=%s", frame.code, frame.reason)
                                break
                            if isinstance(frame, Refused):
                                log.warning("refused status=%s", frame.status)
                                if frame.status == 409:
                                    raise TakenOver(frame.detail)
                                break
                            if isinstance(frame, Gap):
                                self.cursor = max(self.cursor, frame.resumed_at)
                            if isinstance(frame, MessageEvent | Gap | ConnectionState):
                                yield frame
                    finally:
                        reader.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await reader
            except TakenOver:
                raise
            except (OSError, websockets.WebSocketException) as exc:
                log.warning("websocket error %s", type(exc).__name__)
            finally:
                self._open = False
            await self._sleep(BACKOFF[min(failures, len(BACKOFF) - 1)])  # type: ignore[misc]
            failures += 1
