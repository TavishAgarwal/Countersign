from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from countersign.store import db, repo
from tests.store.conftest import make_row


def test_pragmas_and_tables(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"meta", "processed_events", "identities", "proposals", "approvals", "ledger",
            "receipts", "autopilot_rules"} <= names  # fmt: skip


def test_new_file_is_private_and_reopen_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    c = db.open_db(path)
    assert path.stat().st_mode & 0o777 == 0o600
    repo.meta_set(c, "k", "v")
    c.close()
    c2 = db.open_db(path)
    assert repo.meta_get(c2, "k") == "v"
    c2.close()


def test_newer_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    db.open_db(path).close()
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version={db.SCHEMA_VERSION + 1}")
    raw.close()
    with pytest.raises(db.SchemaError):
        db.open_db(path)


def test_transaction_commits_and_rolls_back(conn: sqlite3.Connection) -> None:
    with db.transaction(conn):
        repo.meta_set(conn, "a", "1")
    assert repo.meta_get(conn, "a") == "1"
    with pytest.raises(RuntimeError), db.transaction(conn):
        repo.meta_set(conn, "b", "2")
        raise RuntimeError("boom")
    assert repo.meta_get(conn, "b") is None


def test_foreign_keys_are_enforced(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError), db.transaction(conn):
        conn.execute(
            "INSERT INTO ledger (action_hash, vendor_id, account_last4, amount_cents, memo, "
            "ts_ms, via) VALUES ('nope', 'V', '1234', 1, 'm', 1, 'quorum')"
        )


def test_proposal_roundtrip_and_queries(conn: sqlite3.Connection) -> None:
    a, b = make_row(1), make_row(2, vendor_id="V-NORTH", invoice_number="INV-2000")
    with db.transaction(conn):
        repo.insert_proposal(conn, a)
        repo.insert_proposal(conn, b)
    assert repo.get_by_short_id(conn, a.short_id) == a
    assert repo.get_by_hash(conn, b.action_hash) == b
    assert repo.get_by_short_id(conn, "CS-zzzzzz") is None
    assert [p.short_id for p in repo.list_open(conn)] == [a.short_id, b.short_id]
    assert [p.short_id for p in repo.open_for_invoice(conn, "V-ACME", "INV-1001")] == [a.short_id]
    assert set(repo.short_ids(conn)) == {a.short_id, b.short_id}
    assert [p.action_hash for p in repo.expired_open(conn, 901_000)] == [
        a.action_hash,
        b.action_hash,
    ]
    assert repo.expired_open(conn, 900_999) == []


def test_duplicate_proposal_is_rejected(conn: sqlite3.Connection) -> None:
    with db.transaction(conn):
        repo.insert_proposal(conn, make_row(1))
    with pytest.raises(sqlite3.IntegrityError), db.transaction(conn):
        repo.insert_proposal(conn, make_row(1))


def test_set_state_and_card(conn: sqlite3.Connection) -> None:
    from countersign.core.models import ProposalState

    row = make_row(1)
    with db.transaction(conn):
        repo.insert_proposal(conn, row)
        repo.set_state(conn, row.action_hash, ProposalState.VOID, superseded_by="CS-bbbbbb")
        repo.set_card_message(conn, row.action_hash, "msg-card")
    got = repo.get_by_hash(conn, row.action_hash)
    assert got is not None and got.state is ProposalState.VOID
    assert got.superseded_by == "CS-bbbbbb" and got.card_message_id == "msg-card"
    assert got.record().superseded_by == "CS-bbbbbb"


def test_events_dedupe_and_clock(conn: sqlite3.Connection) -> None:
    with db.transaction(conn):
        assert repo.mark_processed(conn, "m1", "r", 5000, 1) is True
        assert repo.mark_processed(conn, "m1", "r", 5000, 2) is False
        repo.mark_processed(conn, "m2", "r", 7000, 3)
    assert repo.was_processed(conn, "m1") and not repo.was_processed(conn, "m9")
    assert repo.latest_event_ts(conn) == 7000


def test_pins_never_overwrite(conn: sqlite3.Connection) -> None:
    with db.transaction(conn):
        repo.pin_identity(conn, "meera", "sid-1", 1)
        repo.pin_identity(conn, "meera", "sid-2", 2)
    assert repo.pins(conn) == {"meera": "sid-1"}
