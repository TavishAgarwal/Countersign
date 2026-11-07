"""Earned autopilot (CS-F20): eligibility, rule terms, coverage and revoke triggers.

Pure functions. A rule is granted only by a quorum (the same engine, kind autopilot_grant) and
is revoked at the first anomaly. Decision D11 fixes the terms.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from countersign.core.canon import canonical_bytes, sha256_hex, short_id
from countersign.core.checks import median_cents
from countersign.core.models import (
    AutopilotRule,
    AutopilotState,
    Flag,
    Frozen,
    Policy,
)

_DAY_MS = 86_400_000
_ROUND_TO_CENTS = 10_000  # round the max amount up to a whole $100


class PaidPayment(Frozen):
    """A completed payment to a vendor, as read from the ledger and proposals."""

    vendor_id: str
    amount_cents: int
    account_hash: str
    clean: bool
    via: Literal["quorum", "autopilot"]
    ts_ms: int


class RuleProposal(Frozen):
    """Terms of a rule offered for a k-approval grant."""

    rule: AutopilotRule
    action_hash: str
    short_id: str
    median_cents: int
    clean_runs: int


class Covered(Frozen):
    """The proposal may be paid under this rule with no approvals."""

    rule_id: str


class NotCovered(Frozen):
    """No active rule applies (or the amount is above the rule's maximum)."""

    reason: str


class Revoke(Frozen):
    """The proposal ends the rule. ``new_state`` is REVOKED or EXPIRED."""

    rule_id: str
    trigger: str
    new_state: AutopilotState


def account_hash(account_number: str) -> str:
    """sha256 of an account number (stored instead of the number itself)."""
    return sha256_hex(account_number.encode("utf-8"))


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def rule_max_cents(median: int, band_pct: int) -> int:
    """(100 + band_pct)% of the median, rounded up to a whole $100 (decision D11)."""
    raw = _ceil_div(median * (100 + band_pct), 100)
    return _ceil_div(raw, _ROUND_TO_CENTS) * _ROUND_TO_CENTS


def propose_rule(
    vendor_id: str,
    history: Sequence[PaidPayment],
    rules: Sequence[AutopilotRule],
    policy: Policy,
    now_ts_ms: int,
    last_revoked_ms: int | None = None,
    taken_ids: Sequence[str] = (),
) -> RuleProposal | None:
    """Offer a rule after ``clean_runs`` clean quorum-paid invoices for one vendor.

    The last ``clean_runs`` payments since the last revocation must all be quorum-paid and
    clean, share one account, and each lie within +-``band_pct`` of their median. Returns
    None when autopilot is off, a rule is already PROPOSED/ACTIVE, or the runs do not qualify.
    """
    ap = policy.autopilot
    if not ap.enabled:
        return None
    if any(
        r.vendor_id == vendor_id and r.state in (AutopilotState.PROPOSED, AutopilotState.ACTIVE)
        for r in rules
    ):
        return None
    mine = [
        p
        for p in history
        if p.vendor_id == vendor_id and (last_revoked_ms is None or p.ts_ms > last_revoked_ms)
    ]
    recent = mine[-ap.clean_runs :]
    if len(recent) < ap.clean_runs:
        return None
    if not all(p.clean and p.via == "quorum" for p in recent):
        return None
    if len({p.account_hash for p in recent}) != 1:
        return None
    median = median_cents([p.amount_cents for p in recent])
    low = median * (100 - ap.band_pct)
    high = median * (100 + ap.band_pct)
    if not all(low <= p.amount_cents * 100 <= high for p in recent):
        return None
    terms = AutopilotRule(
        rule_id="AP-000000",
        vendor_id=vendor_id,
        account_hash=recent[-1].account_hash,
        max_cents=rule_max_cents(median, ap.band_pct),
        cap_cents=ap.cap_cents,
        expires_at_ms=now_ts_ms + ap.max_days * _DAY_MS,
        state=AutopilotState.PROPOSED,
    )
    body = terms.model_dump(mode="json", exclude={"rule_id"})
    body["proposed_at_ms"] = now_ts_ms
    digest = sha256_hex(canonical_bytes(body))
    sid = short_id(digest, taken_ids, prefix="AP")
    rule = terms.model_copy(update={"rule_id": sid})
    return RuleProposal(
        rule=rule, action_hash=digest, short_id=sid, median_cents=median, clean_runs=ap.clean_runs
    )


def covers(
    rule: AutopilotRule,
    amount_cents: int,
    account_number: str,
    flags: Sequence[Flag],
    evidence_ok: bool,
    now_ts_ms: int,
) -> Covered | NotCovered | Revoke:
    """Decide whether an ACTIVE rule pays this proposal, ignores it, or is revoked by it.

    Revoke triggers, in order: rule expiry, any flag (its code, e.g. ``BANK_CHANGED``), a
    different account, an evidence mismatch, the cumulative cap. An amount above the rule's
    maximum is simply NotCovered (it needs full approvals; the rule stays).
    """
    if rule.state is not AutopilotState.ACTIVE:
        return NotCovered(reason="rule not active")
    if now_ts_ms >= rule.expires_at_ms:
        return Revoke(rule_id=rule.rule_id, trigger="EXPIRED", new_state=AutopilotState.EXPIRED)
    if flags:
        return _revoke(rule, str(flags[0].code))
    if account_hash(account_number) != rule.account_hash:
        return _revoke(rule, "BANK_CHANGED")
    if not evidence_ok:
        return _revoke(rule, "EVIDENCE_MISMATCH")
    if rule.spent_cents + amount_cents > rule.cap_cents:
        return _revoke(rule, "CAP_REACHED")
    if amount_cents > rule.max_cents:
        return NotCovered(reason="above rule maximum")
    return Covered(rule_id=rule.rule_id)


def _revoke(rule: AutopilotRule, trigger: str) -> Revoke:
    return Revoke(rule_id=rule.rule_id, trigger=trigger, new_state=AutopilotState.REVOKED)


def revoke_manual(rule: AutopilotRule, username: str) -> Revoke:
    """An approver ended the rule with ``autopilot revoke``; the trigger names them."""
    return _revoke(rule, f"revoked by {username}")
