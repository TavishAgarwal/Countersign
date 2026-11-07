"""Earned autopilot wired into the runtime (CS-F20; architecture.md §5.6).

The rules (eligibility, terms, coverage, revoke triggers) live in ``core/autopilot.py``. This module
reads and writes the store and posts the messages: offering a rule after clean payments, granting
it through the normal quorum, evaluating it on new proposals, and revoking it.
"""

from __future__ import annotations

import json
import logging

from countersign.chat import render
from countersign.core import autopilot
from countersign.core.canon import JsonValue
from countersign.core.models import (
    AutopilotRule,
    AutopilotState,
    Flag,
    FlagCode,
    ProposalKind,
    ProposalState,
)
from countersign.runtime import Runtime
from countersign.store import db, receipts, repo
from countersign.switch.models import MessageEvent

log = logging.getLogger("countersign.autopilot")
DAY_MS = 86_400_000
SYSTEM = "countersign"  # initiator of a grant proposal: nobody is excluded from approving it


def _vendor_name(rt: Runtime, vendor_id: str) -> str:
    vendor = rt.vendors.get(vendor_id) if rt.vendors else None
    return vendor.name if vendor else vendor_id


def _ends_on(ms: int) -> str:
    return render.iso_utc(ms)[:10]


def rule_line(rt: Runtime, rule: AutopilotRule) -> str:
    """The one-line summary of an ACTIVE rule."""
    return render.rule_line(
        rule.rule_id, _vendor_name(rt, rule.vendor_id), rule.max_cents, rule.spent_cents,
        rule.cap_cents, _ends_on(rule.expires_at_ms),
    )  # fmt: skip


def active_rule_lines(rt: Runtime) -> list[str]:
    """Lines for ``status`` and ``autopilot list``."""
    return [rule_line(rt, r) for r in repo.rules(rt.conn) if r.state is AutopilotState.ACTIVE]


# ---------------------------------------------------------------- offer


async def offer_rule(rt: Runtime, vendor_id: str, ts_ms: int) -> None:
    """After a quorum payment: propose a rule when the vendor now qualifies."""
    assert rt.rooms is not None and rt.policy is not None
    rows = repo.payment_history(rt.conn, vendor_id)
    proposal = autopilot.propose_rule(
        vendor_id, [r.payment for r in rows], repo.rules(rt.conn), rt.policy, ts_ms,
        repo.last_revoked_ms(rt.conn, vendor_id), repo.short_ids(rt.conn),
    )  # fmt: skip
    if proposal is None:
        return
    rule = proposal.rule
    last4 = rows[-1].account_number[-4:]
    row = repo.ProposalRow(
        action_hash=proposal.action_hash, short_id=proposal.short_id,
        kind=ProposalKind.AUTOPILOT_GRANT, vendor_id=vendor_id, invoice_number=None,
        amount_cents=rule.max_cents, canonical_json=json.dumps(rule.model_dump(mode="json")),
        initiator=SYSTEM, k_required=rt.policy.autopilot.grant_k, needs_callback=False,
        flags_json="[]", policy_hash=rt.policy_hash, state=ProposalState.OPEN,
        opened_at_ms=ts_ms, expires_at_ms=ts_ms + rt.policy.window_ms(vendor_id),
    )  # fmt: skip
    body: dict[str, JsonValue] = {
        "short_id": proposal.short_id, "kind": "autopilot_grant", "vendor_id": vendor_id,
        "max_cents": rule.max_cents, "cap_cents": rule.cap_cents, "policy_hash": rt.policy_hash,
    }  # fmt: skip
    with db.transaction(rt.conn):
        repo.insert_rule(rt.conn, rule, proposal.action_hash)
        repo.insert_proposal(rt.conn, row)
        rcpt = receipts.append(rt.conn, "PROPOSAL_OPENED", proposal.action_hash, body, ts_ms)
    card = render.autopilot_card(
        render.AutopilotCardData(
            rule_id=rule.rule_id, k_required=row.k_required,
            vendor_name=_vendor_name(rt, vendor_id), vendor_id=vendor_id,
            clean_runs=proposal.clean_runs, account_last4=last4, median_cents=proposal.median_cents,
            max_cents=rule.max_cents, cap_cents=rule.cap_cents,
            ends_on=_ends_on(rule.expires_at_ms),
        )
    )  # fmt: skip
    card_id = await rt.say(rt.rooms.approvals, card)
    if card_id:
        with db.transaction(rt.conn):
            repo.set_card_message(rt.conn, proposal.action_hash, card_id)
    await rt.post_receipt(
        rcpt, f"Autopilot offered for {_vendor_name(rt, vendor_id)}", rule.rule_id
    )


# ---------------------------------------------------------------- grant


async def grant(rt: Runtime, row: repo.ProposalRow, approvers: list[str], ts_ms: int) -> str | None:
    """Quorum reached on a grant proposal: activate the rule. Returns the reply line."""
    assert rt.policy is not None
    rule = repo.get_rule(rt.conn, row.short_id)
    if rule is None:
        return None
    expires = ts_ms + rt.policy.autopilot.max_days * DAY_MS
    body: dict[str, JsonValue] = {
        "rule_id": rule.rule_id, "vendor_id": rule.vendor_id, "max_cents": rule.max_cents,
        "cap_cents": rule.cap_cents, "expires_at_ms": expires, "approvers": list(approvers),
        "policy_hash": rt.policy_hash,
    }  # fmt: skip
    with db.transaction(rt.conn):
        repo.activate_rule(rt.conn, rule.rule_id, ts_ms, expires)
        repo.set_state(rt.conn, row.action_hash, ProposalState.GRANTED)
        rcpt = receipts.append(rt.conn, "AUTOPILOT_GRANTED", row.action_hash, body, ts_ms)
    active = repo.get_rule(rt.conn, rule.rule_id)
    assert active is not None
    await rt.post_receipt(
        rcpt, f"Autopilot granted for {_vendor_name(rt, rule.vendor_id)} ({rule.rule_id})",
        f"Approved by {', '.join(approvers)}",
    )  # fmt: skip
    return rule_line(rt, active)


def close_grant(rt: Runtime, row: repo.ProposalRow, why: str) -> None:
    """A grant proposal was rejected or expired: its rule goes back to nothing (inside a txn)."""
    if row.kind is ProposalKind.AUTOPILOT_GRANT:
        repo.close_rule(rt.conn, row.short_id, AutopilotState.EXPIRED, why)


# ---------------------------------------------------------------- use and revoke


class Gate:
    """What the active rule means for a proposal about to open."""

    def __init__(
        self,
        rule: AutopilotRule | None,
        covered: bool,
        revoke: autopilot.Revoke | None,
    ) -> None:
        self.rule, self.covered, self.revoke = rule, covered, revoke


def evaluate(
    rt: Runtime, vendor_id: str, amount_cents: int, account_number: str,
    flags: tuple[Flag, ...], ts_ms: int,
) -> Gate:  # fmt: skip
    """Evaluate the vendor's ACTIVE rule (evidence already matched) against a new proposal."""
    rule = repo.active_rule(rt.conn, vendor_id)
    if rule is None:
        return Gate(None, False, None)
    out = autopilot.covers(rule, amount_cents, account_number, flags, True, ts_ms)
    if isinstance(out, autopilot.Covered):
        return Gate(rule, True, None)
    if isinstance(out, autopilot.Revoke):
        return Gate(rule, False, out)
    return Gate(rule, False, None)


def revoke_flag(rule_id: str) -> Flag:
    """The card flag for an invoice that ends a rule."""
    return Flag(code=FlagCode.AUTOPILOT_REVOKES, rule_id=rule_id)


def apply_revoke(
    rt: Runtime, rev: autopilot.Revoke, ts_ms: int, extra: dict[str, JsonValue]
) -> receipts.Receipt:
    """Close the rule and write the receipt (call inside a transaction)."""
    rule = repo.get_rule(rt.conn, rev.rule_id)
    assert rule is not None
    repo.close_rule(rt.conn, rev.rule_id, rev.new_state, rev.trigger)
    body: dict[str, JsonValue] = {
        "rule_id": rev.rule_id, "vendor_id": rule.vendor_id, "trigger": rev.trigger, **extra
    }  # fmt: skip
    return receipts.append(rt.conn, "AUTOPILOT_REVOKED", None, body, ts_ms)


async def announce_revoke(
    rt: Runtime, rev: autopilot.Revoke, rcpt: receipts.Receipt, invoice: str, short_id: str
) -> None:
    assert rt.rooms is not None
    text = render.autopilot_revoked(rev.rule_id, rev.trigger, invoice, short_id)
    await rt.say(rt.rooms.approvals, text)
    await rt.post_receipt(rcpt, f"Autopilot {rev.rule_id} revoked", f"trigger {rev.trigger}")


async def revoke_for_evidence(rt: Runtime, vendor_id: str, ts_ms: int, invoice: str) -> None:
    """A proposal for an autopilot vendor failed the PDF check: the rule ends too."""
    rule = repo.active_rule(rt.conn, vendor_id)
    if rule is None:
        return
    rev = autopilot.Revoke(
        rule_id=rule.rule_id, trigger="EVIDENCE_MISMATCH", new_state=AutopilotState.REVOKED
    )
    with db.transaction(rt.conn):
        rcpt = apply_revoke(rt, rev, ts_ms, {"invoice": invoice})
    await announce_revoke(rt, rev, rcpt, invoice, "-")


async def manual_revoke(rt: Runtime, event: MessageEvent, target: str) -> None:
    """``autopilot revoke <AP-id | vendor_id>`` by a listed approver."""
    from countersign.decisions import check_sender, reply

    assert rt.rooms is not None
    p = event.payload
    if await check_sender(rt, event, None) is None:
        return
    ids = {v.vendor_id.lower(): v.vendor_id for v in (rt.vendors.vendors if rt.vendors else ())}
    rule = repo.get_rule(rt.conn, target) or repo.active_rule(
        rt.conn, ids.get(target.lower(), target)
    )
    if rule is None or rule.state is not AutopilotState.ACTIVE:
        await reply(rt, event, None, f"No active autopilot rule matches {target}.")
        return
    rev = autopilot.revoke_manual(rule, p.sender_name)
    with db.transaction(rt.conn):
        rcpt = apply_revoke(rt, rev, p.timestamp, {"by": p.sender_name})
    await reply(rt, event, None, render.autopilot_revoked_by(rule.rule_id, p.sender_name))
    await rt.post_receipt(rcpt, f"Autopilot {rule.rule_id} revoked", f"by {p.sender_name}")


async def list_rules(rt: Runtime, event: MessageEvent) -> None:
    from countersign.decisions import reply

    lines = active_rule_lines(rt)
    await reply(rt, event, None, "\n".join(lines) if lines else "No active autopilot rules.")
