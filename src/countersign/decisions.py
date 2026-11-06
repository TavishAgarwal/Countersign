"""Approver commands in ``#ap-approvals`` (CS-F8..F12; architecture §5.3, §6.3).

Order for approve / reject / callback: guard (freeze check) -> identity (layer 2) -> look up the
proposal -> validate the attempt -> record it -> quorum -> one transaction for any state change
(ledger + state + receipt). Messages are posted after the commit.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from countersign import autopilot_flow
from countersign.chat import render
from countersign.core import identity, quorum
from countersign.core.canon import JsonValue
from countersign.core.codes import Code
from countersign.core.commands import Command, CommandKind
from countersign.core.models import (
    Allow,
    Approval,
    ApprovalKind,
    Deny,
    Pending,
    Proposal,
    ProposalKind,
    ProposalState,
    Rejected,
)
from countersign.runtime import Runtime
from countersign.store import db, ledger, receipts, repo
from countersign.switch.models import MessageEvent

log = logging.getLogger("countersign.decisions")
_KIND = {
    CommandKind.APPROVE: ApprovalKind.APPROVE,
    CommandKind.REJECT: ApprovalKind.REJECT,
    CommandKind.CALLBACK: ApprovalKind.CALLBACK,
}


def _thread(event: MessageEvent, row: repo.ProposalRow | None) -> str:
    if row is not None and row.card_message_id:
        return row.card_message_id
    return event.payload.thread_id or event.payload.message_id


async def reply(rt: Runtime, event: MessageEvent, row: repo.ProposalRow | None, text: str) -> None:
    """Reply in the card's thread (or under the user's message when there is no card)."""
    assert rt.rooms is not None
    prefix = row.short_id if row is not None else None
    await rt.say(rt.rooms.approvals, text, _thread(event, row), prefix)


async def check_sender(
    rt: Runtime,
    event: MessageEvent,
    row: repo.ProposalRow | None,
    kind: ApprovalKind = ApprovalKind.APPROVE,
) -> identity.IdentityOk | None:
    """Layer-2 identity. Replies with the reason and returns None when the sender is refused."""
    assert rt.rooms is not None and rt.policy is not None
    p = event.payload
    people = await rt.poster.in_room(rt.rooms.approvals, rt.ops.list_participants)
    ident = identity.IdentityInput(
        room_id=event.room_id, approvals_room_id=rt.rooms.approvals, sender_kind=p.sender_kind,
        sender_name=p.sender_name, sender_id=p.sender, sender_role=p.sender_role,
        participants=tuple(identity.Participant(name=x.name, type=x.type) for x in people),
    )  # fmt: skip
    verdict = identity.check_identity(ident, repo.pins(rt.conn), rt.policy)
    if isinstance(verdict, identity.IdentityOk):
        return verdict
    if row is not None:
        with db.transaction(rt.conn):
            _record(rt, row, p, kind, None, counted=False, code=str(verdict.code))
    await reply(rt, event, row, render.not_counted(p.sender_name, verdict.code))
    return None


def _record(
    rt: Runtime,
    row: repo.ProposalRow,
    p: object,
    kind: ApprovalKind,
    reason: str | None,
    counted: bool,
    code: str | None,
) -> None:
    from countersign.switch.models import MessagePayload

    assert isinstance(p, MessagePayload)
    repo.add_approval(
        rt.conn, row.action_hash,
        Approval(message_id=p.message_id, username=p.sender_name, sender_id=p.sender,
                 decision=kind, reason=reason, ts_ms=p.timestamp, counted=counted),
        code,
    )  # fmt: skip


def _denial_text(rt: Runtime, row: repo.ProposalRow, code: Code, username: str) -> str:
    fields = {
        "time": render.ist_time(row.expires_at_ms),
        "state": row.state.value,
        "new_id": row.superseded_by or "",
        "id": row.short_id,
    }
    return render.not_counted(username, code, **fields)


async def handle_action(rt: Runtime, event: MessageEvent, cmd: Command) -> None:
    """approve / reject / callback ``<id>``."""
    assert rt.rooms is not None and rt.policy is not None and cmd.short_id is not None
    p = event.payload
    frozen = await rt.guard(p.timestamp)
    row = repo.get_by_short_id(rt.conn, cmd.short_id)
    if frozen is not None:
        return await reply(rt, event, row, render.frozen_thread(Code.E_FROZEN))
    assert rt.policy is not None
    ok = await check_sender(rt, event, row, _KIND[cmd.kind])
    if ok is None:
        return
    if ok.pin_needed:
        with db.transaction(rt.conn):
            repo.pin_identity(rt.conn, p.sender_name, p.sender, p.timestamp)
            pin_body: dict[str, JsonValue] = {"username": p.sender_name, "sender_id": p.sender}
            pinned = receipts.append(rt.conn, "IDENTITY_PINNED", None, pin_body, p.timestamp)
        await rt.post_receipt(pinned, f"Identity pinned for {p.sender_name}", "first sight")
    if row is None:
        text = render.not_counted(p.sender_name, Code.E_UNKNOWN_ID, id=cmd.short_id)
        return await reply(rt, event, None, text)
    kind = _KIND[cmd.kind]
    record = row.record()
    prior = repo.approvals_for(rt.conn, row.action_hash)
    denial = quorum.validate_attempt(record, prior, p.sender_name, kind, cmd.reason, p.timestamp)
    if denial is not None:
        return await _deny(rt, event, row, kind, cmd.reason, denial.code)
    with db.transaction(rt.conn):
        _record(rt, row, p, kind, cmd.reason, counted=True, code=None)
    rows = repo.approvals_for(rt.conn, row.action_hash)
    verdict = quorum.decide(record, rows, p.timestamp)
    await _apply(rt, event, row, kind, rows, verdict)


async def _deny(
    rt: Runtime,
    event: MessageEvent,
    row: repo.ProposalRow,
    kind: ApprovalKind,
    reason: str | None,
    code: Code,
) -> None:
    p = event.payload
    rcpt: receipts.Receipt | None = None
    with db.transaction(rt.conn):
        _record(rt, row, p, kind, reason, counted=False, code=str(code))
        if code is Code.E_EXPIRED and row.state is ProposalState.OPEN:
            repo.set_state(rt.conn, row.action_hash, ProposalState.EXPIRED)
            autopilot_flow.close_grant(rt, row, "grant expired")
            body: dict[str, JsonValue] = {"short_id": row.short_id, "code": str(code)}
            rcpt = receipts.append(rt.conn, "EXPIRED", row.action_hash, body, p.timestamp)
    await reply(rt, event, row, _denial_text(rt, row, code, p.sender_name))
    if rcpt is not None:
        n = _counted_approvers(repo.approvals_for(rt.conn, row.action_hash), row)
        text = render.expired(row.expires_at_ms, n, row.k_required, rcpt.seq)
        await reply(rt, event, row, text)
        await rt.post_receipt(
            rcpt, f"Expired with {n} of {row.k_required} approvals", "window closed"
        )


def _counted_approvers(rows: Sequence[Approval], row: repo.ProposalRow) -> int:
    names = {
        a.username for a in rows
        if a.counted and a.decision is ApprovalKind.APPROVE and a.username != row.initiator
    }  # fmt: skip
    return len(names)


async def _apply(
    rt: Runtime,
    event: MessageEvent,
    row: repo.ProposalRow,
    kind: ApprovalKind,
    rows: Sequence[Approval],
    verdict: Pending | Allow | Rejected | Deny,
) -> None:
    p = event.payload
    n = _counted_approvers(rows, row)
    if isinstance(verdict, Deny):
        await reply(rt, event, row, _denial_text(rt, row, verdict.code, p.sender_name))
    elif isinstance(verdict, Pending):
        if kind is ApprovalKind.CALLBACK:
            text = render.callback_counted(p.sender_name)
        else:
            text = render.counted(n, row.k_required, p.sender_name, verdict.callback_missing)
        await reply(rt, event, row, text)
    elif isinstance(verdict, Rejected):
        await _reject(rt, event, row, verdict)
    else:
        if kind is ApprovalKind.CALLBACK:
            await reply(rt, event, row, render.callback_counted(p.sender_name))
        else:
            await reply(rt, event, row, render.counted(n, row.k_required, p.sender_name, False))
        if row.kind is ProposalKind.AUTOPILOT_GRANT:
            await _grant(rt, event, row, rows)
        else:
            await _pay(rt, event, row, rows)


async def _grant(
    rt: Runtime, event: MessageEvent, row: repo.ProposalRow, rows: Sequence[Approval]
) -> None:
    """Quorum reached on an autopilot grant: guard once more, then activate the rule."""
    p = event.payload
    if await rt.guard(p.timestamp) is not None:
        return await reply(rt, event, row, render.frozen_thread(Code.E_FROZEN))
    names = sorted({a.username for a in rows if a.counted and a.decision is ApprovalKind.APPROVE})
    line = await autopilot_flow.grant(rt, row, names, p.timestamp)
    if line is not None:
        await reply(rt, event, row, line)


async def _reject(rt: Runtime, event: MessageEvent, row: repo.ProposalRow, v: Rejected) -> None:
    body: dict[str, JsonValue] = {"short_id": row.short_id, "by": v.by, "reason": v.reason}
    with db.transaction(rt.conn):
        repo.set_state(rt.conn, row.action_hash, ProposalState.REJECTED)
        autopilot_flow.close_grant(rt, row, f"grant rejected by {v.by}")
        rcpt = receipts.append(rt.conn, "REJECTED", row.action_hash, body, event.payload.timestamp)
    await reply(rt, event, row, render.rejected(v.by, v.reason, rcpt.seq))
    await rt.post_receipt(rcpt, f"Rejected by {v.by}", f"reason: {v.reason[:80]}")


async def _pay(
    rt: Runtime,
    event: MessageEvent,
    row: repo.ProposalRow,
    rows: Sequence[Approval],
) -> None:
    """Execute the payment: guard once more, then one transaction (ledger + state + receipt)."""
    p = event.payload
    frozen = await rt.guard(p.timestamp)
    if frozen is not None:
        return await reply(rt, event, row, render.frozen_thread(Code.E_FROZEN))
    proposal = Proposal.model_validate(_canonical_fields(row))
    approvers = sorted(
        {a.username for a in rows if a.counted and a.decision is ApprovalKind.APPROVE
         and a.username != row.initiator}
    )  # fmt: skip
    callbacks = [a.username for a in rows if a.counted and a.decision is ApprovalKind.CALLBACK]
    last4 = proposal.bank.account_number[-4:]
    vendor = rt.vendors.get(proposal.vendor_id) if rt.vendors else None
    callback_by = callbacks[0] if callbacks else None
    body: dict[str, JsonValue] = {
        "short_id": row.short_id,
        "vendor_id": proposal.vendor_id,
        "amount_cents": row.amount_cents or 0,
        "account_last4": last4,
        "approvers": list(approvers),
        "callback_by": callback_by,
        "initiator": row.initiator,
        "policy_hash": row.policy_hash,
        "via": "quorum",
    }
    with db.transaction(rt.conn):
        ledger.pay(
            rt.conn,
            row.action_hash,
            proposal.vendor_id,
            last4,
            proposal.amount_cents,
            proposal.invoice_number,
            p.timestamp,
            "quorum",
        )
        repo.set_state(rt.conn, row.action_hash, ProposalState.PAID)
        rcpt = receipts.append(rt.conn, "PAID", row.action_hash, body, p.timestamp)
    amount = proposal.amount_cents
    await reply(rt, event, row, render.paid(amount, last4, approvers, rcpt.seq))
    await autopilot_flow.offer_rule(rt, proposal.vendor_id, p.timestamp)
    name = vendor.name if vendor else proposal.vendor_id
    line2, line3 = render.paid_lines(name, amount, last4, approvers, callback_by, row.initiator)
    await rt.post_receipt(rcpt, line2, line3)


def _canonical_fields(row: repo.ProposalRow) -> dict[str, object]:
    data = json.loads(row.canonical_json)
    data.pop("policy_hash", None)
    return dict(data)
