"""Proposal handler for ``#ap-intake`` (CS-F4..F7, F13; architecture §5.2, §5.5).

Everything the card shows is built from Countersign's own records: the initiator from the server's
message history, bank and vendor data from the vendor file, evidence from the PDF itself.
Agent text is advisory only.
"""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from countersign import autopilot_flow
from countersign.chat import render
from countersign.core import checks, evidence
from countersign.core.canon import JsonValue, action_hash, short_id
from countersign.core.codes import Code
from countersign.core.commands import extract_json_block
from countersign.core.identity import check_proposer
from countersign.core.models import Flag, Proposal, ProposalKind, ProposalState, last4
from countersign.pdf import PdfError, extract_text
from countersign.runtime import Runtime
from countersign.store import db, ledger, receipts, repo
from countersign.switch import media
from countersign.switch.models import MessageEvent
from countersign.switch.ops import SwitchError

log = logging.getLogger("countersign.proposals")


async def refuse(rt: Runtime, event: MessageEvent, code: Code, **fields: str) -> None:
    """Write a REFUSED receipt and reply in the intake thread with the reason."""
    assert rt.rooms is not None
    p = event.payload
    body: dict[str, JsonValue] = {
        "code": str(code),
        "sender": p.sender_name,
        "message_id": p.message_id,
    }
    with db.transaction(rt.conn):
        rcpt = receipts.append(rt.conn, "REFUSED", None, body, p.timestamp)
    thread = p.thread_id or p.message_id
    await rt.say(rt.rooms.intake, render.intake_refused(code, rcpt.seq, **fields), thread)
    await rt.post_receipt(rcpt, f"Refused: {code}", f"proposer {p.sender_name}")


def parse_proposal(arg: str) -> Proposal | str:
    """Parse the fenced JSON block into a Proposal, or return the first error text."""
    block = extract_json_block(arg)
    if block is None:
        return "expected exactly one fenced json block"
    try:
        return Proposal.model_validate_json(block)
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = ".".join(str(x) for x in err["loc"])
        return f"{loc}: {err['msg']}" if loc else str(err["msg"])


async def find_initiator(rt: Runtime, proposal: Proposal) -> str | None:
    """Server-recorded sender of the message that carried the PDF, or None (fail closed)."""
    assert rt.rooms is not None
    try:
        history = await rt.ops.read_context(rt.rooms.intake, limit=200)
    except SwitchError:
        return None
    for msg in history:
        if msg.get("id") == proposal.source_message_id and msg.get("kind") == "message":
            atts = msg.get("attachments") or []
            if isinstance(atts, list) and any(
                isinstance(a, dict) and a.get("mxc") == proposal.attachment.mxc for a in atts
            ):
                return str(msg.get("sender_name") or "") or None
    return None


async def handle_proposal(rt: Runtime, event: MessageEvent, arg: str) -> None:
    """Handle ``@countersign propose`` + a JSON block from a proposer agent."""
    assert rt.rooms is not None
    p = event.payload
    thread = p.thread_id or p.message_id
    frozen = await rt.guard(p.timestamp)
    if frozen is not None:
        await rt.say(rt.rooms.intake, render.frozen_thread(Code.E_FROZEN), thread)
        return
    assert rt.policy is not None and rt.vendors is not None
    if check_proposer(p.sender_name, rt.policy) is not None:
        return await refuse(rt, event, Code.E_NOT_PROPOSER)
    parsed = parse_proposal(arg)
    if isinstance(parsed, str):
        return await refuse(rt, event, Code.E_BAD_PROPOSAL, error=parsed)
    initiator = await find_initiator(rt, parsed)
    if initiator is None:
        return await refuse(rt, event, Code.E_NO_SOURCE)
    paid = repo.paid_amounts(rt.conn, parsed.vendor_id)
    result = checks.evaluate(parsed, rt.vendors, paid, rt.policy)
    if result.refusal is not None or result.vendor is None:
        return await refuse(rt, event, result.refusal or Code.E_INTERNAL, name=parsed.vendor_id)
    try:
        pdf_bytes = await rt.ops.download_media(rt.rooms.intake, parsed.attachment.mxc)
        text = extract_text(pdf_bytes)
    except (media.MediaError, PdfError):
        return await refuse(rt, event, Code.E_PDF_UNREADABLE)
    match = evidence.match(parsed, text)
    if not match.ok:
        await autopilot_flow.revoke_for_evidence(
            rt, parsed.vendor_id, p.timestamp, parsed.invoice_number
        )
        field = ", ".join(match.mismatched) or "text"
        return await refuse(rt, event, match.code or Code.E_INTERNAL, field=field)
    await open_proposal(rt, event, parsed, initiator, result)


async def open_proposal(
    rt: Runtime,
    event: MessageEvent,
    proposal: Proposal,
    initiator: str,
    result: checks.CheckResult,
) -> None:
    """Store the proposal (superseding open ones); pay it under an autopilot rule or post a card."""
    assert rt.rooms is not None and rt.policy is not None and result.vendor is not None
    vendor = result.vendor
    p = event.payload
    thread = p.thread_id or p.message_id
    canonical, ahash = action_hash(proposal, rt.policy_hash)
    existing = repo.get_by_hash(rt.conn, ahash)
    if existing is not None:
        if existing.state is ProposalState.OPEN:
            text = render.intake_accepted(
                existing.short_id, existing.k_required, existing.needs_callback
            )
            await rt.say(rt.rooms.intake, text, thread)
            return
        return await refuse(rt, event, Code.E_NOT_OPEN, state=existing.state.value)
    try:
        sid = short_id(ahash, repo.short_ids(rt.conn))
    except ValueError:
        return await refuse(rt, event, Code.E_INTERNAL)
    gate = autopilot_flow.evaluate(
        rt, proposal.vendor_id, proposal.amount_cents, proposal.bank.account_number,
        result.flags, p.timestamp,
    )  # fmt: skip
    if gate.revoke is not None:
        flag = autopilot_flow.revoke_flag(gate.revoke.rule_id)
        result = result.model_copy(update={"flags": (*result.flags, flag)})
    window_ms = rt.policy.window_ms(proposal.vendor_id)
    row = repo.ProposalRow(
        action_hash=ahash, short_id=sid, kind=ProposalKind.PAYMENT, vendor_id=proposal.vendor_id,
        invoice_number=proposal.invoice_number, amount_cents=proposal.amount_cents,
        canonical_json=canonical.decode("utf-8"), initiator=initiator,
        k_required=result.k_required, needs_callback=result.needs_callback,
        flags_json=json.dumps([f.model_dump(mode="json") for f in result.flags]),
        policy_hash=rt.policy_hash,
        state=ProposalState.PAID if gate.covered else ProposalState.OPEN,
        opened_at_ms=p.timestamp, expires_at_ms=p.timestamp + window_ms,
        intake_room_id=rt.rooms.intake, proposal_message_id=p.message_id,
    )  # fmt: skip
    olds = repo.open_for_invoice(rt.conn, proposal.vendor_id, proposal.invoice_number)
    opened_body: dict[str, JsonValue] = {
        "short_id": sid, "vendor_id": proposal.vendor_id, "invoice_number": proposal.invoice_number,
        "amount_cents": proposal.amount_cents, "initiator": initiator, "proposer": p.sender_name,
        "k_required": result.k_required, "needs_callback": result.needs_callback,
        "flags": [str(f.code) for f in result.flags], "policy_hash": rt.policy_hash,
    }  # fmt: skip
    new_receipts: list[receipts.Receipt] = []
    revoke_receipt: receipts.Receipt | None = None
    paid_receipt: receipts.Receipt | None = None
    last4_ = last4(proposal.bank.account_number)
    with db.transaction(rt.conn):
        for old in olds:
            repo.set_state(rt.conn, old.action_hash, ProposalState.VOID, superseded_by=sid)
            void_body: dict[str, JsonValue] = {"code": str(Code.E_SUPERSEDED), "superseded_by": sid,
                                               "short_id": old.short_id}  # fmt: skip
            new_receipts.append(
                receipts.append(rt.conn, "VOID", old.action_hash, void_body, p.timestamp)
            )
        repo.insert_proposal(rt.conn, row)
        new_receipts.append(
            receipts.append(rt.conn, "PROPOSAL_OPENED", ahash, opened_body, p.timestamp)
        )
        if gate.covered and gate.rule is not None:
            ledger.pay(
                rt.conn, ahash, proposal.vendor_id, last4_, proposal.amount_cents,
                proposal.invoice_number, p.timestamp, "autopilot",
            )  # fmt: skip
            repo.add_spent(rt.conn, gate.rule.rule_id, proposal.amount_cents)
            paid_body: dict[str, JsonValue] = {
                "short_id": sid, "rule_id": gate.rule.rule_id, "vendor_id": proposal.vendor_id,
                "amount_cents": proposal.amount_cents, "account_last4": last4_,
                "initiator": initiator, "policy_hash": rt.policy_hash, "via": "autopilot",
            }  # fmt: skip
            paid_receipt = receipts.append(rt.conn, "AUTOPILOT_PAID", ahash, paid_body, p.timestamp)
        elif gate.revoke is not None:
            revoke_receipt = autopilot_flow.apply_revoke(
                rt, gate.revoke, p.timestamp, {"invoice": proposal.invoice_number, "short_id": sid}
            )
    for old in olds:
        if old.card_message_id:
            await rt.say(rt.rooms.approvals, render.void(sid), old.card_message_id, old.short_id)
    if paid_receipt is not None and gate.rule is not None:
        text = render.autopilot_paid(
            proposal.amount_cents, last4_, gate.rule.rule_id, paid_receipt.seq
        )
        await rt.say(rt.rooms.intake, text, thread)
        await rt.post_receipt(
            paid_receipt,
            f"{vendor.name} · {render.usd(proposal.amount_cents)} → ****{last4_}",
            f"Autopilot {gate.rule.rule_id} · initiator {initiator}",
        )  # fmt: skip
        return
    card = render.card(card_data(rt, row, proposal, result.flags, vendor.name, vendor.phone))
    card_id = await rt.say(rt.rooms.approvals, card)
    if card_id:
        with db.transaction(rt.conn):
            repo.set_card_message(rt.conn, ahash, card_id)
    if olds:
        text = render.intake_superseding(sid, olds[0].short_id)
    else:
        text = render.intake_accepted(sid, result.k_required, result.needs_callback)
    await rt.say(rt.rooms.intake, text, thread)
    if revoke_receipt is not None and gate.revoke is not None:
        await autopilot_flow.announce_revoke(
            rt, gate.revoke, revoke_receipt, proposal.invoice_number, sid
        )
    for rcpt in new_receipts:
        line2 = (
            f"{rcpt.type} {sid}"
            if rcpt.type == "PROPOSAL_OPENED"
            else "Replaced by a newer proposal"
        )
        await rt.post_receipt(rcpt, line2, f"initiator {initiator}")


def card_data(
    rt: Runtime,
    row: repo.ProposalRow,
    proposal: Proposal,
    flags: tuple[Flag, ...],
    vendor_name: str,
    vendor_phone: str,
) -> render.CardData:
    """Assemble the card from stored records (also used to re-render ``status <id>``)."""
    assert rt.policy is not None
    return render.CardData(
        short_id=row.short_id, action_hash=row.action_hash, policy_hash=row.policy_hash,
        k_required=row.k_required, needs_callback=row.needs_callback, vendor_id=proposal.vendor_id,
        vendor_name=vendor_name, vendor_phone=vendor_phone, amount_cents=proposal.amount_cents,
        invoice_number=proposal.invoice_number, due_date=proposal.due_date,
        account_last4=last4(proposal.bank.account_number),
        routing_number=proposal.bank.routing_number,
        flags=flags, initiator=row.initiator, expires_at_ms=row.expires_at_ms,
        window_sec=(row.expires_at_ms - row.opened_at_ms) // 1000,
        intake_note=proposal.intake_note, intake_flags=proposal.intake_flags,
    )  # fmt: skip
