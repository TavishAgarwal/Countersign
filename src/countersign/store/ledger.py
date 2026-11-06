"""Mock bank: insert-only ledger, one row per action hash (CS-F11, FR-1).

Only Countersign writes here (rules.md golden rule 3). A UNIQUE constraint on the action hash and
triggers against UPDATE/DELETE make a double payment impossible.
"""

from __future__ import annotations

import sqlite3
from typing import Literal

from countersign.core.models import Frozen


class LedgerError(RuntimeError):
    """The payment was not recorded (for example a second payment for the same action)."""


class LedgerEntry(Frozen):
    """One paid row."""

    id: int
    action_hash: str
    vendor_id: str
    account_last4: str
    amount_cents: int
    memo: str
    ts_ms: int
    via: Literal["quorum", "autopilot"]


def pay(
    conn: sqlite3.Connection,
    action_hash: str,
    vendor_id: str,
    account_last4: str,
    amount_cents: int,
    memo: str,
    ts_ms: int,
    via: Literal["quorum", "autopilot"],
) -> int:
    """Insert one ledger row and return its id. Call inside ``transaction()``.

    Raises LedgerError when a payment already exists for ``action_hash`` or the amount is not
    positive; the caller must then roll back the whole decision.
    """
    try:
        cur = conn.execute(
            "INSERT INTO ledger (action_hash, vendor_id, account_last4, amount_cents, memo, "
            "ts_ms, via) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (action_hash, vendor_id, account_last4, amount_cents, memo, ts_ms, via),
        )
    except sqlite3.IntegrityError as exc:
        raise LedgerError(str(exc)) from exc
    assert cur.lastrowid is not None
    return cur.lastrowid


def entries(conn: sqlite3.Connection) -> list[LedgerEntry]:
    """All paid rows, oldest first."""
    rows = conn.execute("SELECT * FROM ledger ORDER BY id").fetchall()
    return [LedgerEntry.model_validate(dict(r)) for r in rows]


def total_cents(conn: sqlite3.Connection) -> int:
    """Sum of everything paid."""
    return int(conn.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM ledger").fetchone()[0])
