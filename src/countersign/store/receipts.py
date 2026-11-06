"""Append-only hash-chained receipts and chain verification (CS-F12, FR-8).

``digest = sha256(prev_digest + canonical(body))`` with a genesis ``prev_digest`` of 64 zeros.
The stored body repeats ``type``, ``action_hash`` and ``ts_ms`` so editing any column of a row
breaks the chain.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Literal

from countersign.core.canon import JsonValue, canonical_bytes, sha256_hex
from countersign.core.models import Frozen

GENESIS = "0" * 64

ReceiptType = Literal[
    "POLICY_BOOTSTRAP",
    "POLICY_RATIFIED",
    "POLICY_ACCEPTED_BY_OWNER",
    "IDENTITY_PINNED",
    "PROPOSAL_OPENED",
    "REFUSED",
    "REJECTED",
    "EXPIRED",
    "VOID",
    "PAID",
    "AUTOPILOT_GRANTED",
    "AUTOPILOT_PAID",
    "AUTOPILOT_REVOKED",
    "FROZEN",
    "UNFROZEN",
]


class Receipt(Frozen):
    """A stored receipt row."""

    seq: int
    type: str
    action_hash: str | None
    body: dict[str, object]
    prev_digest: str
    digest: str
    ts_ms: int


class ChainStatus(Frozen):
    """Result of verify(): ``first_bad_seq`` is set when ``ok`` is False."""

    ok: bool
    count: int
    head: str
    first_bad_seq: int | None = None
    reason: str = ""


def _digest(prev: str, body_json: str) -> str:
    return sha256_hex((prev + body_json).encode("utf-8"))


def head_digest(conn: sqlite3.Connection) -> str:
    """Digest of the newest receipt, or the genesis value when there are none."""
    row = conn.execute("SELECT digest FROM receipts ORDER BY seq DESC LIMIT 1").fetchone()
    return str(row["digest"]) if row else GENESIS


def append(
    conn: sqlite3.Connection,
    rtype: ReceiptType,
    action_hash: str | None,
    body: dict[str, JsonValue],
    ts_ms: int,
) -> Receipt:
    """Append one receipt and return it. Call inside ``transaction()`` with the decision.

    ``body`` carries the receipt's facts (reason codes, usernames, amounts in cents ...).
    """
    full: dict[str, JsonValue] = {**body, "type": rtype, "action_hash": action_hash, "ts_ms": ts_ms}
    body_json = canonical_bytes(full).decode("utf-8")
    prev = head_digest(conn)
    digest = _digest(prev, body_json)
    cur = conn.execute(
        "INSERT INTO receipts (type, action_hash, body_json, prev_digest, digest, ts_ms) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (rtype, action_hash, body_json, prev, digest, ts_ms),
    )
    seq = cur.lastrowid
    assert seq is not None
    return Receipt(
        seq=seq, type=rtype, action_hash=action_hash, body=dict(full), prev_digest=prev,
        digest=digest, ts_ms=ts_ms,
    )  # fmt: skip


def get(conn: sqlite3.Connection, seq: int) -> Receipt | None:
    """Return one receipt by sequence number."""
    row = conn.execute("SELECT * FROM receipts WHERE seq = ?", (seq,)).fetchone()
    return _row(row) if row else None


def _row(row: sqlite3.Row) -> Receipt:
    return Receipt(
        seq=row["seq"], type=row["type"], action_hash=row["action_hash"],
        body=json.loads(row["body_json"]), prev_digest=row["prev_digest"],
        digest=row["digest"], ts_ms=row["ts_ms"],
    )  # fmt: skip


def verify(conn: sqlite3.Connection) -> ChainStatus:
    """Re-check the whole chain and the ledger/receipt correspondence.

    Fails at the first receipt whose digest, link, sequence, columns or body disagree, and when a
    ledger row has no PAID / AUTOPILOT_PAID receipt for the same action hash.
    """
    prev, expected_seq, count = GENESIS, 1, 0
    for row in conn.execute("SELECT * FROM receipts ORDER BY seq"):
        count += 1
        seq = int(row["seq"])
        problem = ""
        if seq != expected_seq:
            problem = "sequence gap"
        elif row["prev_digest"] != prev:
            problem = "broken link"
        elif _digest(prev, row["body_json"]) != row["digest"]:
            problem = "digest mismatch"
        else:
            try:
                body = json.loads(row["body_json"])
            except ValueError:
                body = None
            same = (
                isinstance(body, dict)
                and body.get("type") == row["type"]
                and body.get("action_hash") == row["action_hash"]
                and body.get("ts_ms") == row["ts_ms"]
            )
            if not same:
                problem = "columns differ from body"
        if problem:
            return ChainStatus(ok=False, count=count, head=prev, first_bad_seq=seq, reason=problem)
        prev, expected_seq = row["digest"], expected_seq + 1
    orphan = conn.execute(
        "SELECT l.action_hash FROM ledger l WHERE NOT EXISTS (SELECT 1 FROM receipts r "
        "WHERE r.action_hash = l.action_hash AND r.type IN ('PAID', 'AUTOPILOT_PAID')) LIMIT 1"
    ).fetchone()
    if orphan:
        return ChainStatus(
            ok=False, count=count, head=prev, first_bad_seq=None,
            reason=f"ledger row without receipt ({orphan['action_hash'][:8]})",
        )  # fmt: skip
    return ChainStatus(ok=True, count=count, head=prev)
