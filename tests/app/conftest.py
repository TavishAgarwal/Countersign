"""A fake Switch (ops client) and builders for the end-to-end handler tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from countersign import proposals
from countersign.config import Config
from countersign.core.addressing import Rule
from countersign.runtime import Rooms, Runtime
from countersign.store import db
from countersign.switch.models import (
    DocumentRef,
    MessageEvent,
    MessagePayload,
    RoomInfo,
    RoomSummary,
    SwitchParticipant,
)
from countersign.switch.ops import SwitchError
from countersign.switch.poster import Poster
from tests.conftest import ROOT, T0

INTAKE, APPROVALS, AUDIT = "R-INT", "R-APP", "R-AUD"
PDF_TEXT = "ACME INV-1040 Total 480.00 account 7700004412"
PEOPLE = ("meera", "arjun", "kabir", "riya")


class FakeOps:
    """Just enough of OpsClient: rooms, participants, documents, posting, history."""

    def __init__(self) -> None:
        self.room = ""
        self.posts: list[tuple[str, str | None, str]] = []
        self.rules: tuple[Rule, ...] = (
            Rule(
                rooms=(APPROVALS,),
                users=tuple(f"u-{n}" for n in ("meera", "arjun", "kabir")),
                agents=(),
            ),
        )
        self.policy_text = (ROOT / "switch" / "policy.yaml").read_text()
        self.vendors_text = (ROOT / "switch" / "vendors.yaml").read_text()
        self.history: list[dict[str, object]] = [
            {
                "id": "msg-1",
                "kind": "message",
                "sender_name": "riya",
                "attachments": [{"mxc": "mxc://srv/abc"}],
            }
        ]
        self.fail_reads = False
        self.server_time_ms = 0
        self._n = 0

    async def list_all_rooms(self) -> tuple[RoomSummary, ...]:
        return (
            RoomSummary(room_id=INTAKE, name="ap-intake"),
            RoomSummary(room_id=APPROVALS, name="ap-approvals"),
            RoomSummary(room_id=AUDIT, name="ap-audit"),
        )

    async def connect_to_room(self, room_id: str) -> RoomInfo:
        self.room = room_id
        docs = (DocumentRef(id="d1", name="policy.yaml"), DocumentRef(id="d2", name="vendors.yaml"))
        return RoomInfo(room_id=room_id, documents=docs if room_id == APPROVALS else ())

    async def load_documents(self, ids: list[str]) -> dict[str, str]:
        if self.fail_reads:
            raise SwitchError(500, "down")
        return {"d1": self.policy_text, "d2": self.vendors_text}

    async def list_participants(self) -> tuple[SwitchParticipant, ...]:
        users = [SwitchParticipant(id=f"u-{n}", name=n, type="user") for n in PEOPLE]
        return (*users, SwitchParticipant(id="a-intake", name="intake", type="agent"))

    async def addressing_rules(self) -> tuple[Rule, ...]:
        return self.rules

    async def post_message(self, body: str, thread_id: str | None = None) -> str:
        self._n += 1
        self.posts.append((self.room, thread_id, body))
        return f"posted-{self._n}"

    async def post_message_http(self, room_id: str, content: str) -> None:
        self.posts.append((room_id, None, content))

    async def read_context(
        self, room_id: str, limit: int = 50, since: str | None = None
    ) -> list[dict[str, object]]:
        return list(self.history) if room_id == INTAKE else []

    async def download_media(self, room_id: str, mxc: str) -> bytes:
        return b"%PDF"

    def said(self, room: str) -> list[str]:
        return [b for r, _, b in self.posts if r == room]


@pytest.fixture
def ops() -> FakeOps:
    return FakeOps()


@pytest.fixture
async def rt(
    tmp_path: Path, ops: FakeOps, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Runtime]:
    monkeypatch.setattr(proposals, "extract_text", lambda _b: PDF_TEXT)
    cfg = Config(
        switch_url="http://x", agent_id="a", api_key="k", db_path=tmp_path / "t.db", mode="ws",
        log_level="INFO", room_intake="ap-intake", room_approvals="ap-approvals",
        room_audit="ap-audit", admin_id=None, root=tmp_path,
    )  # fmt: skip
    conn = db.open_db(cfg.db_path)
    poster = Poster(ops)  # type: ignore[arg-type]
    runtime = Runtime(cfg, conn, ops, poster)  # type: ignore[arg-type]
    worker = asyncio.create_task(poster.run())
    runtime.rooms = Rooms(INTAKE, APPROVALS, AUDIT)
    yield runtime
    worker.cancel()
    conn.close()


_seq = 0


def event(
    room: str, sender: str, body: str, ts: int = T0, thread: str | None = None
) -> MessageEvent:
    global _seq
    _seq += 1
    return MessageEvent(
        room_id=room,
        sequence=_seq,
        payload=MessagePayload(
            addressed=True, sender=f"u-{sender}", sender_name=sender, message_id=f"m{_seq}",
            body=body, timestamp=ts, thread_id=thread,
        ),
    )  # fmt: skip


def propose_body(**over: object) -> str:
    import json

    raw: dict[str, object] = {
        "type": "payment_proposal", "schema": 1, "source_message_id": "msg-1",
        "attachment": {"filename": "INV-1040.pdf", "mxc": "mxc://srv/abc"},
        "vendor_id": "V-ACME", "invoice_number": "INV-1040", "invoice_date": "2026-11-01",
        "due_date": "2026-11-15", "amount_cents": 48000, "currency": "USD",
        "bank": {"account_number": "7700004412", "routing_number": "021000021"},
    }  # fmt: skip
    raw.update(over)
    return "@countersign propose\n```json\n" + json.dumps(raw) + "\n```"
