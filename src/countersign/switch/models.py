"""Pydantic models for Switch frames, events and op results (spikes S1-S6, protocol 9).

Switch adds fields over time, so these models ignore unknown fields (rules.md §2).
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict


class SwitchModel(BaseModel):
    """Base for models of Switch data."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class Attachment(SwitchModel):
    """A file pointer on a message (bytes come from the media route)."""

    filename: str = ""
    mimetype: str = ""
    size: int = 0
    mxc: str = ""
    msgtype: str = ""


class MessagePayload(SwitchModel):
    """The payload of a ``message`` event. ``sender_kind`` and ``sender_role`` are null in 0.31."""

    addressed: bool = False
    sender: str
    sender_name: str
    sender_kind: str | None = None
    sender_role: str | None = None
    message_id: str
    body: str = ""
    timestamp: int
    thread_id: str | None = None
    attachments: tuple[Attachment, ...] = ()


class MessageEvent(SwitchModel):
    """A message event with its room and sequence."""

    room_id: str
    sequence: int
    payload: MessagePayload


class ConnectionState(SwitchModel):
    """First frame of every connection."""

    connection_id: str
    generation: int
    cursor: int
    rooms: tuple[str, ...] = ()


class Gap(SwitchModel):
    """Events were lost; resume from ``resumed_at`` and re-read history."""

    resumed_at: int = 0
    from_sequence: int = 0
    rooms: tuple[str, ...] = ()
    all_rooms: bool = False
    reason: str = ""


class Evicted(SwitchModel):
    """The server dropped the connection. ``taken_over`` is final; others may reopen."""

    code: str = ""
    reason: str = ""


class Refused(SwitchModel):
    """The server refused the open."""

    status: int = 0
    detail: str = ""


class Ping(SwitchModel):
    """Keepalive; answer with a pong carrying the processed cursor, before anything else."""


class Other(SwitchModel):
    """A frame Countersign ignores (subscription_changed, room_released, room_join ...)."""

    name: str = ""


Frame = MessageEvent | ConnectionState | Gap | Evicted | Refused | Ping | Other


def parse_frame(raw: str | bytes) -> Frame:
    """Parse one websocket text frame. Unknown or malformed frames become ``Other``."""
    try:
        obj = json.loads(raw)
    except ValueError:
        return Other(name="invalid")
    if not isinstance(obj, dict):
        return Other(name="invalid")
    name = str(obj.get("event") or obj.get("type") or "")
    data = obj.get("data")
    body: Mapping[str, object] = data if isinstance(data, dict) else obj
    try:
        if name == "ping":
            return Ping()
        if name == "message":
            return MessageEvent.model_validate(
                {
                    "room_id": body["room_id"],
                    "sequence": body["sequence"],
                    "payload": body["payload"],
                }
            )
        if name == "connection_state":
            return ConnectionState.model_validate(body)
        if name == "gap":
            return Gap.model_validate(body)
        if name == "evicted":
            return Evicted.model_validate(body)
        if name == "refused":
            return Refused.model_validate(body)
    except (KeyError, ValueError):
        return Other(name=f"bad {name}")
    return Other(name=name)


# ---------------------------------------------------------------- op results


class SwitchParticipant(SwitchModel):
    """An entry of ``list_participants``: ``type`` is ``user`` or ``agent``."""

    id: str
    name: str
    type: str


class DocumentRef(SwitchModel):
    """A room document listed in the ``connect_to_room`` payload."""

    id: str
    name: str = ""


class RoomInfo(SwitchModel):
    """The useful part of ``connect_to_room``."""

    room_id: str
    name: str = ""
    participants: tuple[SwitchParticipant, ...] = ()
    documents: tuple[DocumentRef, ...] = ()


class RoomSummary(SwitchModel):
    """An entry of ``list_all_rooms``."""

    room_id: str
    name: str = ""
