from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from countersign.core.models import ProposalKind, ProposalState
from countersign.store import db
from countersign.store.repo import ProposalRow


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.open_db(tmp_path / "t.db")
    yield c
    c.close()


def make_row(n: int = 1, **over: object) -> ProposalRow:
    h = f"{n:02x}" * 32
    raw: dict[str, object] = {
        "action_hash": h, "short_id": f"CS-{h[:6]}", "kind": ProposalKind.PAYMENT,
        "vendor_id": "V-ACME", "invoice_number": f"INV-{1000 + n}", "amount_cents": 48000,
        "canonical_json": "{}", "initiator": "riya", "k_required": 1, "needs_callback": False,
        "flags_json": "[]", "policy_hash": "p" * 64, "state": ProposalState.OPEN,
        "opened_at_ms": 1000, "expires_at_ms": 901_000,
    }  # fmt: skip
    raw.update(over)
    return ProposalRow.model_validate(raw)
