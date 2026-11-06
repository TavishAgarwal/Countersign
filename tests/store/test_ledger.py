from __future__ import annotations

import sqlite3

import pytest

from countersign.store import db, ledger, receipts, repo
from tests.store.conftest import make_row


def seed(conn: sqlite3.Connection, n: int = 1) -> str:
    row = make_row(n)
    with db.transaction(conn):
        repo.insert_proposal(conn, row)
    return row.action_hash


def test_pay_inserts_one_row(conn: sqlite3.Connection) -> None:
    h = seed(conn)
    with db.transaction(conn):
        ledger.pay(conn, h, "V-ACME", "4412", 48000, "INV-1001", 5000, "quorum")
    got = ledger.entries(conn)
    assert len(got) == 1 and got[0].amount_cents == 48000 and got[0].via == "quorum"
    assert ledger.total_cents(conn) == 48000
    assert repo.paid_amounts(conn, "V-ACME") == [48000]


def test_second_payment_for_the_same_action_is_impossible(conn: sqlite3.Connection) -> None:
    h = seed(conn)
    with db.transaction(conn):
        ledger.pay(conn, h, "V-ACME", "4412", 48000, "m", 1, "quorum")
    with pytest.raises(ledger.LedgerError), db.transaction(conn):
        ledger.pay(conn, h, "V-ACME", "4412", 48000, "m", 2, "quorum")
    assert len(ledger.entries(conn)) == 1


def test_non_positive_amount_is_refused(conn: sqlite3.Connection) -> None:
    h = seed(conn)
    with pytest.raises(ledger.LedgerError), db.transaction(conn):
        ledger.pay(conn, h, "V-ACME", "4412", 0, "m", 1, "quorum")


def test_ledger_is_insert_only(conn: sqlite3.Connection) -> None:
    h = seed(conn)
    with db.transaction(conn):
        ledger.pay(conn, h, "V-ACME", "4412", 48000, "m", 1, "quorum")
    with pytest.raises(sqlite3.IntegrityError, match="insert-only"):
        conn.execute("UPDATE ledger SET amount_cents = 1")
    with pytest.raises(sqlite3.IntegrityError, match="insert-only"):
        conn.execute("DELETE FROM ledger")


def test_decision_transaction_is_all_or_nothing(conn: sqlite3.Connection) -> None:
    """T14: ledger row, state change and receipt commit together or not at all."""
    h = seed(conn)
    with pytest.raises(RuntimeError), db.transaction(conn):
        ledger.pay(conn, h, "V-ACME", "4412", 48000, "m", 1, "quorum")
        receipts.append(conn, "PAID", h, {"x": 1}, 1)
        raise RuntimeError("crash before commit")
    assert ledger.entries(conn) == []
    assert receipts.verify(conn).count == 0


def test_verify_flags_a_ledger_row_without_a_receipt(conn: sqlite3.Connection) -> None:
    h = seed(conn)
    with db.transaction(conn):
        ledger.pay(conn, h, "V-ACME", "4412", 48000, "m", 1, "quorum")
    status = receipts.verify(conn)
    assert not status.ok and "without receipt" in status.reason
    with db.transaction(conn):
        receipts.append(conn, "PAID", h, {}, 2)
    assert receipts.verify(conn).ok
