"""Quorum predicate (CS-F8): Allow / Pending / Rejected / Deny, with injected server time."""

from __future__ import annotations

from collections.abc import Sequence

from countersign.core.codes import Code
from countersign.core.models import (
    Allow,
    Approval,
    ApprovalKind,
    Decision,
    Deny,
    Pending,
    ProposalRecord,
    ProposalState,
    Rejected,
)


def _guard(record: ProposalRecord, now_ts_ms: int) -> Deny | None:
    """State and window checks shared by validate_attempt() and decide()."""
    if record.state is ProposalState.VOID and record.superseded_by:
        return Deny(code=Code.E_SUPERSEDED)
    if record.state is not ProposalState.OPEN:
        return Deny(code=Code.E_NOT_OPEN)
    if now_ts_ms >= record.expires_at_ms:
        return Deny(code=Code.E_EXPIRED)
    return None


def validate_attempt(
    record: ProposalRecord,
    prior: Sequence[Approval],
    username: str,
    kind: ApprovalKind,
    reason: str | None,
    now_ts_ms: int,
) -> Deny | None:
    """Decide whether an identity-checked attempt may be counted. None means countable.

    Inputs: the proposal row, earlier attempts, the sender's username, what they typed, an
    optional reason and the event's server timestamp. Reason codes, in order: ``E_SUPERSEDED``,
    ``E_NOT_OPEN``, ``E_EXPIRED``, ``E_REASON_REQUIRED`` (reject without a reason),
    ``E_INITIATOR`` (approve or callback by the uploader; a reject is allowed because it only
    makes things safer), ``E_DUPLICATE`` (same user, same kind, already counted).
    """
    denied = _guard(record, now_ts_ms)
    if denied is not None:
        return denied
    if kind is ApprovalKind.REJECT and not (reason and reason.strip()):
        return Deny(code=Code.E_REASON_REQUIRED)
    if kind is not ApprovalKind.REJECT and username == record.initiator:
        return Deny(code=Code.E_INITIATOR)
    if any(a.counted and a.username == username and a.decision is kind for a in prior):
        return Deny(code=Code.E_DUPLICATE)
    return None


def decide(record: ProposalRecord, attempts: Sequence[Approval], now_ts_ms: int) -> Decision:
    """Tally counted attempts for one proposal.

    Inputs: the proposal row, its attempts (only ``counted`` ones are used) and the event's
    server timestamp. Output:
    - ``Deny(E_SUPERSEDED | E_NOT_OPEN | E_EXPIRED)`` when the proposal is no longer actionable
    - ``Rejected`` when any counted reject exists
    - ``Allow`` when distinct non-initiator approvals >= k and any needed callback is present
    - ``Deny(E_CALLBACK_REQUIRED)`` when approvals are complete but the callback is missing
      (the proposal stays OPEN; a later callback can still complete it)
    - ``Pending(n, k, callback_missing)`` otherwise
    """
    denied = _guard(record, now_ts_ms)
    if denied is not None:
        return denied
    rows = [a for a in attempts if a.counted]
    for a in rows:
        if a.decision is ApprovalKind.REJECT:
            return Rejected(by=a.username, reason=a.reason or "")
    approvers = {
        a.username
        for a in rows
        if a.decision is ApprovalKind.APPROVE and a.username != record.initiator
    }
    called_back = any(
        a.decision is ApprovalKind.CALLBACK and a.username != record.initiator for a in rows
    )
    n, k = len(approvers), record.k_required
    callback_missing = record.needs_callback and not called_back
    if k >= 1 and n >= k:
        if callback_missing:
            return Deny(code=Code.E_CALLBACK_REQUIRED)
        return Allow()
    return Pending(n=n, k=k, callback_missing=callback_missing)
