from __future__ import annotations

import sqlite3

import pytest

from countersign.store import db, receipts


def add(conn: sqlite3.Connection, n: int) -> None:
    with db.transaction(conn):
        receipts.append(conn, "PROPOSAL_OPENED", f"h{n}", {"n": n, "code": None}, 1000 + n)


def tamper(conn: sqlite3.Connection, sql: str, *args: object) -> None:
    """An attacker with file access: drop the guard trigger, then edit a row."""
    conn.execute("DROP TRIGGER receipts_no_update")
    conn.execute(sql, args)


def test_empty_chain_is_ok(conn: sqlite3.Connection) -> None:
    s = receipts.verify(conn)
    assert s.ok and s.count == 0 and s.head == receipts.GENESIS


def test_chain_links_and_verifies(conn: sqlite3.Connection) -> None:
    for n in range(1, 6):
        add(conn, n)
    s = receipts.verify(conn)
    assert s.ok and s.count == 5
    first, second = receipts.get(conn, 1), receipts.get(conn, 2)
    assert first is not None and second is not None
    assert first.prev_digest == receipts.GENESIS and second.prev_digest == first.digest
    assert s.head == receipts.get(conn, 5).digest  # type: ignore[union-attr]


def test_body_is_canonical_and_carries_its_columns(conn: sqlite3.Connection) -> None:
    add(conn, 1)
    r = receipts.get(conn, 1)
    assert r is not None
    assert r.body == {
        "n": 1,
        "code": None,
        "type": "PROPOSAL_OPENED",
        "action_hash": "h1",
        "ts_ms": 1001,
    }
    raw = conn.execute("SELECT body_json FROM receipts").fetchone()[0]
    assert raw == '{"action_hash":"h1","code":null,"n":1,"ts_ms":1001,"type":"PROPOSAL_OPENED"}'


def test_receipts_are_append_only(conn: sqlite3.Connection) -> None:
    add(conn, 1)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE receipts SET ts_ms = 1")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM receipts")


@pytest.mark.parametrize(
    ("sql", "bad_seq", "reason"),
    [
        (
            "UPDATE receipts SET body_json = replace(body_json, '\"n\":3', '\"n\":9') WHERE seq = 3",
            3,
            "digest mismatch",
        ),
        ("UPDATE receipts SET digest = ? WHERE seq = 2", 2, "digest mismatch"),
        ("UPDATE receipts SET prev_digest = ? WHERE seq = 4", 4, "broken link"),
        ("UPDATE receipts SET ts_ms = 5 WHERE seq = 2", 2, "columns differ from body"),
        ("UPDATE receipts SET type = 'PAID' WHERE seq = 5", 5, "columns differ from body"),
        ("UPDATE receipts SET action_hash = 'x' WHERE seq = 1", 1, "columns differ from body"),
    ],
)
def test_any_edit_is_detected_at_the_right_row(
    conn: sqlite3.Connection, sql: str, bad_seq: int, reason: str
) -> None:
    for n in range(1, 6):
        add(conn, n)
    args = ("f" * 64,) if "?" in sql else ()
    tamper(conn, sql, *args)
    s = receipts.verify(conn)
    assert not s.ok and s.first_bad_seq == bad_seq and s.reason == reason


def test_deleted_row_is_detected(conn: sqlite3.Connection) -> None:
    for n in range(1, 5):
        add(conn, n)
    conn.execute("DROP TRIGGER receipts_no_delete")
    conn.execute("DELETE FROM receipts WHERE seq = 2")
    s = receipts.verify(conn)
    assert not s.ok and s.first_bad_seq == 3 and s.reason == "sequence gap"
