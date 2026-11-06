"""Shared runtime state: policy, rooms, freeze state, the guard and posting helpers.

The guard (startup, before every proposal/approval/payment, and while idle) fails closed: any
problem with the policy documents, their ratification, or Countersign's own addressing rules
freezes payments (architecture §5.7, §11.1, threats T10, T11, T15).
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass

from countersign.chat import render
from countersign.config import Config
from countersign.core import addressing
from countersign.core.canon import JsonValue
from countersign.core.codes import Code
from countersign.core.models import Policy, VendorBook
from countersign.core.policy import PolicyError, parse_documents
from countersign.store import db, receipts, repo
from countersign.switch.models import SwitchParticipant
from countersign.switch.ops import OpsClient, SwitchError
from countersign.switch.poster import Poster

log = logging.getLogger("countersign.runtime")
DOC_NAMES = ("policy.yaml", "vendors.yaml")


@dataclass(frozen=True)
class Rooms:
    """Room ids by role, resolved by name at startup."""

    intake: str
    approvals: str
    audit: str


@dataclass(frozen=True)
class Freeze:
    """Why payments are paused."""

    code: Code
    detail: str


class StartupError(RuntimeError):
    """A prerequisite is missing before the guard can even run (rooms, documents)."""


class Runtime:
    """Everything the handlers share. Single consumer: never used concurrently."""

    def __init__(
        self, cfg: Config, conn: sqlite3.Connection, ops: OpsClient, poster: Poster
    ) -> None:
        self.cfg, self.conn, self.ops, self.poster = cfg, conn, ops, poster
        self.rooms: Rooms | None = None
        self.doc_room: str | None = None
        self.policy: Policy | None = None
        self.vendors: VendorBook | None = None
        self.policy_hash = ""
        self.freeze: Freeze | None = None
        self.addressing_ok = False
        self.lock = asyncio.Lock()
        self._freeze_loaded = False
        self.texts: tuple[str, str] = ("", "")

    # ------------------------------------------------------------ rooms and time

    async def locate_rooms(self) -> Rooms:
        """Find the three rooms by name. Raises StartupError naming what is missing."""
        rooms = await self.ops.list_all_rooms()
        by_name = {r.name: r.room_id for r in rooms}
        for name in (self.cfg.room_intake, self.cfg.room_approvals, self.cfg.room_audit):
            if sum(r.name == name for r in rooms) > 1:
                raise StartupError(f"more than one room is named {name}; delete the duplicates")
        wanted = (self.cfg.room_intake, self.cfg.room_approvals, self.cfg.room_audit)
        missing = [n for n in wanted if n not in by_name]
        if missing:
            raise StartupError(f"rooms not found: {', '.join(missing)}")
        self.rooms = Rooms(*(by_name[n] for n in wanted))
        return self.rooms

    async def server_now(self) -> int:
        """Server time in ms: the newest event or room message timestamp (never the local clock)."""
        assert self.rooms is not None
        newest = repo.latest_event_ts(self.conn)
        try:
            msgs = await self.ops.read_context(self.rooms.approvals, limit=5)
        except SwitchError:
            return max(newest, self.ops.server_time_ms)
        stamps = (int(str(m.get("timestamp", 0))) for m in msgs)
        return max([newest, self.ops.server_time_ms, *stamps])

    # ------------------------------------------------------------ documents and guard

    async def fetch_documents(self) -> tuple[str, str]:
        assert self.rooms is not None
        rooms = [self.doc_room] if self.doc_room else [self.rooms.approvals, self.rooms.intake,
                                                      self.rooms.audit]  # fmt: skip
        for room_id in filter(None, rooms):

            async def read(room_id: str = room_id) -> dict[str, str]:
                info = await self.ops.connect_to_room(room_id)
                ids = {d.name: d.id for d in info.documents if d.name in DOC_NAMES}
                if len(ids) != len(DOC_NAMES):
                    return {}
                texts = await self.ops.load_documents(list(ids.values()))
                return {name: texts.get(i, "") for name, i in ids.items()}

            found = await self.poster.in_room(room_id, read)
            if found:
                self.doc_room = room_id
                return found["policy.yaml"], found["vendors.yaml"]
        raise StartupError("policy.yaml and vendors.yaml room documents not found")

    async def _approver_ids(self, policy: Policy) -> set[str]:
        assert self.rooms is not None
        people: tuple[SwitchParticipant, ...] = await self.poster.in_room(
            self.rooms.approvals, self.ops.list_participants
        )
        return {p.id for p in people if p.type == "user" and p.name in policy.approvers}

    async def guard(self, ts_ms: int) -> Freeze | None:
        """Re-check policy documents, ratification and addressing; update the freeze state.

        Returns the current Freeze (None when payments are active). Never raises: any
        failure to check becomes a Freeze (fail closed).
        """
        assert self.rooms is not None
        cause: Freeze | None = None
        try:
            texts = await self.fetch_documents()
            policy, vendors, phash = parse_documents(*texts)
            self.texts = texts
            self.policy, self.vendors, self.policy_hash = policy, vendors, phash
            cause = self._ratification(phash, ts_ms)
            if cause is None:
                rules = await self.ops.addressing_rules()
                ids = await self._approver_ids(policy)
                code = addressing.check_addressing(
                    rules, self.rooms.approvals, ids, self.cfg.admin_id
                )
                self.addressing_ok = code is None
                if code is not None:
                    cause = Freeze(code, "addressing rules")
        except PolicyError as exc:
            cause = Freeze(Code.E_SELF_CHECK, f"policy documents ({exc})")
        except (StartupError, SwitchError) as exc:
            cause = Freeze(Code.E_SELF_CHECK, f"cannot check ({type(exc).__name__})")
        await self._transition(cause, ts_ms)
        return self.freeze

    def _ratification(self, phash: str, ts_ms: int) -> Freeze | None:
        ratified = repo.meta_get(self.conn, "ratified_policy_hash")
        if ratified is None:
            with db.transaction(self.conn):
                repo.meta_set(self.conn, "ratified_policy_hash", phash)
                repo.meta_set(self.conn, "ratified_policy_text", self.texts[0])
                repo.meta_set(self.conn, "ratified_vendors_text", self.texts[1])
                receipts.append(self.conn, "POLICY_BOOTSTRAP", None, {"policy_hash": phash}, ts_ms)
            return None
        if ratified != phash:
            return Freeze(Code.E_POLICY_UNRATIFIED, "policy.yaml or vendors.yaml")
        return None

    async def _transition(self, cause: Freeze | None, ts_ms: int) -> None:
        assert self.rooms is not None
        if not self._freeze_loaded:
            self._freeze_loaded = True
            last = repo.last_freeze(self.conn)
            if last is not None and last[0] == "FROZEN":
                self.freeze = Freeze(Code(last[1]), "restored from the last receipt")
        before = self.freeze
        same = (before is None and cause is None) or (
            before is not None and cause is not None and before.code == cause.code
        )
        if same:
            return
        self.freeze = cause
        body: dict[str, JsonValue] = {"policy_hash": self.policy_hash}
        with db.transaction(self.conn):
            if cause is not None:
                body.update(code=str(cause.code), detail=cause.detail)
                rcpt = receipts.append(self.conn, "FROZEN", None, body, ts_ms)
            else:
                rcpt = receipts.append(self.conn, "UNFROZEN", None, body, ts_ms)
        log.warning("freeze state code=%s", cause.code if cause else "NONE")
        text = (
            render.frozen_banner(
                cause.code,
                owner_accept=cause.code is Code.E_POLICY_UNRATIFIED,
                docs=cause.detail,
            )
            if cause is not None
            else render.unfrozen(self.policy_hash)
        )
        await self.say(self.rooms.approvals, text)
        await self.post_receipt(rcpt, "Payments frozen" if cause else "Payments resumed",
                                cause.detail if cause else "checks passed")  # fmt: skip

    # ------------------------------------------------------------ posting helpers

    async def say(
        self, room_id: str, text: str, thread_id: str | None = None, prefix: str | None = None
    ) -> str:
        """Post a message; failures are logged and never break a decision already committed."""
        try:
            return await self.poster.post(room_id, text, thread_id, prefix)
        except SwitchError as exc:
            log.error("post failed room=%s status=%s", room_id, exc.status)
            return ""

    async def post_receipt(self, rcpt: receipts.Receipt, line2: str, line3: str) -> None:
        """Post the five-line receipt to ``#ap-audit``."""
        assert self.rooms is not None
        ref = rcpt.action_hash[:9] if rcpt.action_hash else "-"
        short = repo.get_by_hash(self.conn, rcpt.action_hash) if rcpt.action_hash else None
        text = render.receipt(
            rcpt.seq, rcpt.type, short.short_id if short else ref, line2, line3,
            self.policy_hash or "0" * 64, rcpt.ts_ms, rcpt.digest, rcpt.prev_digest,
        )  # fmt: skip
        await self.say(self.rooms.audit, text)
