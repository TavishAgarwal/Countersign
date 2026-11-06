"""Shared builders for the pure-core tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from countersign.core.models import (
    Approval,
    ApprovalKind,
    Policy,
    Proposal,
    ProposalKind,
    ProposalRecord,
    ProposalState,
    VendorBook,
)
from countersign.core.policy import parse_documents

ROOT = Path(__file__).resolve().parent.parent
T0 = 1_788_684_000_000  # an arbitrary server timestamp (ms)


@pytest.fixture(scope="session")
def docs() -> tuple[Policy, VendorBook, str]:
    return parse_documents(
        (ROOT / "switch" / "policy.yaml").read_text(),
        (ROOT / "switch" / "vendors.yaml").read_text(),
    )


@pytest.fixture(scope="session")
def policy(docs: tuple[Policy, VendorBook, str]) -> Policy:
    return docs[0]


@pytest.fixture(scope="session")
def vendors(docs: tuple[Policy, VendorBook, str]) -> VendorBook:
    return docs[1]


@pytest.fixture(scope="session")
def phash(docs: tuple[Policy, VendorBook, str]) -> str:
    return docs[2]


def make_proposal(**over: object) -> Proposal:
    """INV-1040 by default: ACME, $480.00, the account on file."""
    raw: dict[str, object] = {
        "type": "payment_proposal",
        "schema": 1,
        "source_message_id": "msg-1",
        "attachment": {"filename": "INV-1040.pdf", "mxc": "mxc://srv/abc"},
        "vendor_id": "V-ACME",
        "invoice_number": "INV-1040",
        "invoice_date": "2026-11-01",
        "due_date": "2026-11-15",
        "amount_cents": 48000,
        "currency": "USD",
        "bank": {"account_number": "7700004412", "routing_number": "021000021"},
    }
    raw.update(over)
    return Proposal.model_validate(raw)


def make_record(**over: object) -> ProposalRecord:
    raw: dict[str, object] = {
        "action_hash": "a" * 64,
        "short_id": "CS-aaaaaa",
        "kind": ProposalKind.PAYMENT,
        "initiator": "riya",
        "k_required": 2,
        "needs_callback": False,
        "state": ProposalState.OPEN,
        "opened_at_ms": T0,
        "expires_at_ms": T0 + 900_000,
    }
    raw.update(over)
    return ProposalRecord.model_validate(raw)


def make_approval(
    username: str,
    kind: ApprovalKind = ApprovalKind.APPROVE,
    *,
    counted: bool = True,
    reason: str | None = None,
    ts: int = T0 + 1000,
    n: int = 0,
) -> Approval:
    return Approval(
        message_id=f"m-{username}-{kind.value}-{n}",
        username=username,
        sender_id=f"id-{username}",
        decision=kind,
        reason=reason,
        ts_ms=ts,
        counted=counted,
    )
