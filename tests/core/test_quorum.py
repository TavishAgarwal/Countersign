from __future__ import annotations

import pytest

from countersign.core.codes import Code
from countersign.core.models import (
    Allow,
    ApprovalKind,
    Deny,
    Pending,
    ProposalState,
    Rejected,
)
from countersign.core.quorum import decide, validate_attempt
from tests.conftest import T0, make_approval, make_record

A, R, C = ApprovalKind.APPROVE, ApprovalKind.REJECT, ApprovalKind.CALLBACK
NOW = T0 + 5000


def test_one_of_one_allows() -> None:
    assert decide(make_record(k_required=1), [make_approval("kabir")], NOW) == Allow()


def test_zero_approvals_is_pending() -> None:
    assert decide(make_record(), [], NOW) == Pending(n=0, k=2, callback_missing=False)


def test_one_of_two_is_pending() -> None:
    assert decide(make_record(), [make_approval("meera")], NOW) == Pending(
        n=1, k=2, callback_missing=False
    )


def test_two_of_two_allows() -> None:
    rows = [make_approval("meera"), make_approval("arjun")]
    assert decide(make_record(), rows, NOW) == Allow()


def test_three_of_two_still_allows() -> None:
    rows = [make_approval(u) for u in ("meera", "arjun", "kabir")]
    assert decide(make_record(), rows, NOW) == Allow()


def test_same_user_twice_counts_once() -> None:
    rows = [make_approval("meera", n=1), make_approval("meera", n=2)]
    assert decide(make_record(), rows, NOW) == Pending(n=1, k=2, callback_missing=False)


def test_initiator_approval_never_counts_even_if_stored() -> None:
    rows = [make_approval("riya"), make_approval("meera")]
    assert decide(make_record(), rows, NOW) == Pending(n=1, k=2, callback_missing=False)


def test_uncounted_rows_are_ignored() -> None:
    rows = [make_approval("meera"), make_approval("arjun", counted=False)]
    assert decide(make_record(), rows, NOW) == Pending(n=1, k=2, callback_missing=False)


def test_any_reject_wins_over_enough_approvals() -> None:
    rows = [make_approval("meera"), make_approval("arjun"), make_approval("kabir", R, reason="no")]
    assert decide(make_record(), rows, NOW) == Rejected(by="kabir", reason="no")


def test_uncounted_reject_does_not_reject() -> None:
    rows = [
        make_approval("meera"),
        make_approval("arjun"),
        make_approval("kabir", R, counted=False),
    ]
    assert decide(make_record(), rows, NOW) == Allow()


def test_callback_needed_and_missing_with_enough_approvals_denies() -> None:
    rows = [make_approval("meera"), make_approval("kabir")]
    res = decide(make_record(needs_callback=True), rows, NOW)
    assert res == Deny(code=Code.E_CALLBACK_REQUIRED)


def test_callback_needed_pending_reports_missing() -> None:
    res = decide(make_record(needs_callback=True), [make_approval("meera")], NOW)
    assert res == Pending(n=1, k=2, callback_missing=True)


def test_callback_present_allows() -> None:
    rows = [make_approval("meera"), make_approval("meera", C), make_approval("kabir")]
    assert decide(make_record(needs_callback=True), rows, NOW) == Allow()


def test_callback_by_initiator_does_not_satisfy() -> None:
    rows = [make_approval("riya", C), make_approval("meera"), make_approval("kabir")]
    res = decide(make_record(needs_callback=True), rows, NOW)
    assert res == Deny(code=Code.E_CALLBACK_REQUIRED)


def test_callback_alone_is_not_an_approval() -> None:
    res = decide(make_record(needs_callback=True), [make_approval("meera", C)], NOW)
    assert res == Pending(n=0, k=2, callback_missing=False)


def test_callback_not_needed_is_ignored() -> None:
    rows = [make_approval("meera"), make_approval("arjun")]
    assert decide(make_record(needs_callback=False), rows, NOW) == Allow()


@pytest.mark.parametrize(
    ("state", "superseded_by", "code"),
    [
        (ProposalState.PAID, None, Code.E_NOT_OPEN),
        (ProposalState.REJECTED, None, Code.E_NOT_OPEN),
        (ProposalState.REFUSED, None, Code.E_NOT_OPEN),
        (ProposalState.EXPIRED, None, Code.E_NOT_OPEN),
        (ProposalState.VOID, "CS-bbbbbb", Code.E_SUPERSEDED),
        (ProposalState.VOID, None, Code.E_NOT_OPEN),
    ],
)
def test_closed_proposals_deny(state: ProposalState, superseded_by: str | None, code: Code) -> None:
    rec = make_record(state=state, superseded_by=superseded_by)
    assert decide(rec, [], NOW) == Deny(code=code)
    assert validate_attempt(rec, [], "meera", A, None, NOW) == Deny(code=code)


def test_expiry_uses_server_time_and_is_closed_at_the_boundary() -> None:
    rec = make_record()
    assert validate_attempt(rec, [], "meera", A, None, rec.expires_at_ms - 1) is None
    assert validate_attempt(rec, [], "meera", A, None, rec.expires_at_ms) == Deny(
        code=Code.E_EXPIRED
    )
    assert decide(rec, [], rec.expires_at_ms + 1) == Deny(code=Code.E_EXPIRED)


def test_validate_initiator() -> None:
    assert validate_attempt(make_record(), [], "riya", A, None, NOW) == Deny(code=Code.E_INITIATOR)
    assert validate_attempt(make_record(), [], "riya", C, None, NOW) == Deny(code=Code.E_INITIATOR)


def test_initiator_may_reject_because_it_only_makes_things_safer() -> None:
    assert validate_attempt(make_record(), [], "riya", R, "wrong vendor", NOW) is None


def test_validate_duplicate() -> None:
    prior = [make_approval("arjun")]
    assert validate_attempt(make_record(), prior, "arjun", A, None, NOW) == Deny(
        code=Code.E_DUPLICATE
    )
    assert validate_attempt(make_record(), prior, "meera", A, None, NOW) is None


def test_uncounted_prior_attempt_is_not_a_duplicate() -> None:
    prior = [make_approval("arjun", counted=False)]
    assert validate_attempt(make_record(), prior, "arjun", A, None, NOW) is None


def test_same_user_may_approve_and_callback() -> None:
    prior = [make_approval("meera", C)]
    assert validate_attempt(make_record(needs_callback=True), prior, "meera", A, None, NOW) is None


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_reject_requires_a_reason(reason: str | None) -> None:
    res = validate_attempt(make_record(), [], "meera", R, reason, NOW)
    assert res == Deny(code=Code.E_REASON_REQUIRED)


def test_state_is_checked_before_initiator() -> None:
    rec = make_record(state=ProposalState.PAID)
    assert validate_attempt(rec, [], "riya", A, None, NOW) == Deny(code=Code.E_NOT_OPEN)


def test_k_zero_never_allows() -> None:
    assert isinstance(decide(make_record(k_required=0), [], NOW), Pending)


def test_a8_payee_swap_sequence() -> None:
    """Old id is superseded; the new id needs a callback even with 2 approvals."""
    old = make_record(state=ProposalState.VOID, superseded_by="CS-bbbbbb")
    assert validate_attempt(old, [], "arjun", A, None, NOW) == Deny(code=Code.E_SUPERSEDED)
    new = make_record(short_id="CS-bbbbbb", needs_callback=True)
    first = [make_approval("meera")]
    assert decide(new, first, NOW) == Pending(n=1, k=2, callback_missing=True)
    both = [*first, make_approval("kabir")]
    assert decide(new, both, NOW) == Deny(code=Code.E_CALLBACK_REQUIRED)
