"""Poll transport: ``read_context`` every 2 s on each room (CS-F2, architecture §5.8).

Used when the websocket is unusable for 60 s, or as the default if spike S1 had failed. It yields
the same MessageEvent objects; dedupe happens in the consumer by message id.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Protocol

from countersign.switch.models import MessageEvent, MessagePayload
from countersign.switch.ops import SwitchError

INTERVAL = 2.0


class PollOps(Protocol):
    """The slice of OpsClient the poller uses."""

    async def read_context(
        self, room_id: str, limit: int = 50, since: str | None = None
    ) -> list[dict[str, object]]: ...


def to_event(room_id: str, msg: dict[str, object], sequence: int) -> MessageEvent | None:
    """Turn a ``read_context`` message into a MessageEvent (None for non-messages)."""
    if msg.get("kind") != "message":
        return None
    attachments = msg.get("attachments") or []
    return MessageEvent(
        room_id=room_id,
        sequence=sequence,
        payload=MessagePayload.model_validate(
            {
                "addressed": True,
                "sender": msg["sender"],
                "sender_name": msg["sender_name"],
                "message_id": msg["id"],
                "body": msg.get("body", ""),
                "timestamp": msg["timestamp"],
                "thread_id": msg.get("thread_id"),
                "attachments": attachments,
            }
        ),
    )


async def poll_events(
    ops: PollOps,
    room_ids: Sequence[str],
    self_name: str,
    since_ts_ms: int,
    is_new: Callable[[str], bool],
    interval: float = INTERVAL,
    sleep: Callable[[float], object] = asyncio.sleep,
) -> AsyncIterator[MessageEvent]:
    """Yield new messages that mention Countersign, oldest first, forever.

    ``is_new(message_id)`` tells whether the consumer has not handled it yet. Messages sent by
    Countersign itself and older than ``since_ts_ms`` are skipped. Network errors are retried.
    """
    sequence = 0
    mention = f"@{self_name}".lower()
    while True:
        batch: list[tuple[str, dict[str, object]]] = []
        for room in room_ids:
            try:
                msgs = await ops.read_context(room)
            except SwitchError:
                continue
            batch += [(room, m) for m in msgs]
        batch.sort(key=lambda rm: int(str(rm[1].get("timestamp", 0))))
        for room, msg in batch:
            body = str(msg.get("body", "")).lower()
            if (
                mention in body
                and msg.get("sender_name") != self_name
                and int(str(msg.get("timestamp", 0))) > since_ts_ms
                and is_new(str(msg.get("id")))
            ):
                sequence += 1
                event = to_event(room, msg, sequence)
                if event:
                    yield event
        await sleep(interval)  # type: ignore[misc]
