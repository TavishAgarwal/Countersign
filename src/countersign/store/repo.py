"""Typed queries over the schema (architecture.md §7). Writes happen inside ``transaction()``."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from countersign.core.autopilot import PaidPayment, account_hash
from countersign.core.models import (
    Approval,
    ApprovalKind,
    AutopilotRule,
    AutopilotState,
    Frozen,
    ProposalKind,
    ProposalRecord,
    ProposalState,
)


class ProposalRow(Frozen):
    """A stored proposal (all columns). Server-derived fields only (initiator, times)."""

    action_hash: str
    short_id: str
    kind: ProposalKind
    vendor_id: str | None
    invoice_number: str | None
    amount_cents: int | None
    canonical_json: str
    initiator: str
    k_required: int
    needs_callback: bool
    flags_json: str
    policy_hash: str
    state: ProposalState
    opened_at_ms: int
    expires_at_ms: int
    card_message_id: str | None = None
    intake_room_id: str | None = None
    proposal_message_id: str | None = None
    superseded_by: str | None = None

    def record(self) -> ProposalRecord:
        """The subset the quorum needs."""
        return ProposalRecord(
            action_hash=self.action_hash, short_id=self.short_id, kind=self.kind,
            initiator=self.initiator, k_required=self.k_required,
            needs_callback=self.needs_callback, state=self.state,
            opened_at_ms=self.opened_at_ms, expires_at_ms=self.expires_at_ms,
            superseded_by=self.superseded_by,
        )  # fmt: skip


# ---------------------------------------------------------------- meta


def meta_get(conn: sqlite3.Connection, key: str) -> str | None:
    """Read a meta value."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else None


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Write a meta value (upsert)."""
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# ---------------------------------------------------------------- events and identities


def mark_processed(
    conn: sqlite3.Connection, message_id: str, room_id: str, ts_ms: int, processed_at_ms: int
) -> bool:
    """Record an event as handled. Returns False when it was already handled (duplicate)."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO processed_events (message_id, room_id, ts_ms, processed_at_ms) "
        "VALUES (?, ?, ?, ?)",
        (message_id, room_id, ts_ms, processed_at_ms),
    )
    return cur.rowcount == 1


def was_processed(conn: sqlite3.Connection, message_id: str) -> bool:
    """True when this event id has been handled."""
    row = conn.execute("SELECT 1 FROM processed_events WHERE message_id = ?", (message_id,))
    return row.fetchone() is not None


def last_freeze(conn: sqlite3.Connection) -> tuple[str, str] | None:
    """The most recent FROZEN or UNFROZEN receipt as (type, code); None when there is none."""
    row = conn.execute(
        "SELECT type, body_json FROM receipts WHERE type IN ('FROZEN', 'UNFROZEN') "
        "ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return str(row["type"]), str(json.loads(row["body_json"]).get("code", ""))


def latest_event_ts(conn: sqlite3.Connection) -> int:
    """Newest server timestamp seen (0 when none); the clock for expiry sweeps."""
    return int(conn.execute("SELECT COALESCE(MAX(ts_ms), 0) FROM processed_events").fetchone()[0])


def pins(conn: sqlite3.Connection) -> dict[str, str]:
    """username -> pinned sender id."""
    rows = conn.execute("SELECT username, sender_id FROM identities").fetchall()
    return {r["username"]: r["sender_id"] for r in rows}


def pin_identity(conn: sqlite3.Connection, username: str, sender_id: str, ts_ms: int) -> None:
    """Pin a username's sender id on first sight. Never overwrites an existing pin."""
    conn.execute(
        "INSERT OR IGNORE INTO identities (username, sender_id, pinned_ts_ms) VALUES (?, ?, ?)",
        (username, sender_id, ts_ms),
    )


# ---------------------------------------------------------------- proposals


def insert_proposal(conn: sqlite3.Connection, p: ProposalRow) -> None:
    """Insert a new proposal row (raises sqlite3.IntegrityError on a duplicate hash or id)."""
    conn.execute(
        "INSERT INTO proposals (action_hash, short_id, kind, vendor_id, invoice_number, "
        "amount_cents, canonical_json, initiator, k_required, needs_callback, flags_json, "
        "policy_hash, state, opened_at_ms, expires_at_ms, card_message_id, intake_room_id, "
        "proposal_message_id, superseded_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            p.action_hash, p.short_id, p.kind.value, p.vendor_id, p.invoice_number,
            p.amount_cents, p.canonical_json, p.initiator, p.k_required, int(p.needs_callback),
            p.flags_json, p.policy_hash, p.state.value, p.opened_at_ms, p.expires_at_ms,
            p.card_message_id, p.intake_room_id, p.proposal_message_id, p.superseded_by,
        ),
    )  # fmt: skip


def _proposal(row: sqlite3.Row | None) -> ProposalRow | None:
    if row is None:
        return None
    data = dict(row)
    data["needs_callback"] = bool(data["needs_callback"])
    return ProposalRow.model_validate(data)


def get_by_short_id(conn: sqlite3.Connection, short_id: str) -> ProposalRow | None:
    """Find a proposal by its exact short id (``CS-xxxxxx`` or ``CS-xxxxxxxx``)."""
    row = conn.execute("SELECT * FROM proposals WHERE short_id = ?", (short_id,)).fetchone()
    return _proposal(row)


def get_by_hash(conn: sqlite3.Connection, action_hash: str) -> ProposalRow | None:
    """Find a proposal by its full action hash."""
    row = conn.execute("SELECT * FROM proposals WHERE action_hash = ?", (action_hash,)).fetchone()
    return _proposal(row)


def short_ids(conn: sqlite3.Connection) -> list[str]:
    """Every short id in use (for collision handling)."""
    return [r["short_id"] for r in conn.execute("SELECT short_id FROM proposals")]


def open_for_invoice(
    conn: sqlite3.Connection, vendor_id: str, invoice_number: str
) -> list[ProposalRow]:
    """OPEN payment proposals for the same vendor and invoice number (supersede targets)."""
    rows = conn.execute(
        "SELECT * FROM proposals WHERE kind = 'payment' AND state = 'OPEN' "
        "AND vendor_id = ? AND invoice_number = ?",
        (vendor_id, invoice_number),
    ).fetchall()
    return [p for r in rows if (p := _proposal(r)) is not None]


def list_open(conn: sqlite3.Connection) -> list[ProposalRow]:
    """All OPEN proposals, oldest first."""
    rows = conn.execute("SELECT * FROM proposals WHERE state = 'OPEN' ORDER BY opened_at_ms")
    return [p for r in rows.fetchall() if (p := _proposal(r)) is not None]


def list_all(conn: sqlite3.Connection) -> list[ProposalRow]:
    """Every proposal, oldest first (CLI status)."""
    rows = conn.execute("SELECT * FROM proposals ORDER BY opened_at_ms, short_id").fetchall()
    return [p for r in rows if (p := _proposal(r)) is not None]


def expired_open(conn: sqlite3.Connection, now_ms: int) -> list[ProposalRow]:
    """OPEN proposals whose window has closed at server time ``now_ms``."""
    rows = conn.execute(
        "SELECT * FROM proposals WHERE state = 'OPEN' AND expires_at_ms <= ?", (now_ms,)
    ).fetchall()
    return [p for r in rows if (p := _proposal(r)) is not None]


def set_state(
    conn: sqlite3.Connection,
    action_hash: str,
    state: ProposalState,
    superseded_by: str | None = None,
) -> None:
    """Move a proposal to a new state (optionally recording the replacing short id)."""
    conn.execute(
        "UPDATE proposals SET state = ?, superseded_by = COALESCE(?, superseded_by) "
        "WHERE action_hash = ?",
        (state.value, superseded_by, action_hash),
    )


def set_card_message(conn: sqlite3.Connection, action_hash: str, message_id: str) -> None:
    """Remember the id of the card message (thread root for replies)."""
    conn.execute(
        "UPDATE proposals SET card_message_id = ? WHERE action_hash = ?", (message_id, action_hash)
    )


def paid_amounts(conn: sqlite3.Connection, vendor_id: str) -> list[int]:
    """Amounts (cents) already paid to a vendor, oldest first (baseline for anomaly checks)."""
    rows = conn.execute(
        "SELECT amount_cents FROM ledger WHERE vendor_id = ? ORDER BY id", (vendor_id,)
    )
    return [int(r["amount_cents"]) for r in rows.fetchall()]


# ---------------------------------------------------------------- approvals


def add_approval(
    conn: sqlite3.Connection,
    action_hash: str,
    a: Approval,
    code: str | None = None,
) -> None:
    """Record every attempt that reached Countersign, counted or not."""
    conn.execute(
        "INSERT INTO approvals (action_hash, message_id, username, sender_id, decision, reason, "
        "ts_ms, counted, code) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            action_hash, a.message_id, a.username, a.sender_id, a.decision.value, a.reason,
            a.ts_ms, int(a.counted), code,
        ),
    )  # fmt: skip


def approvals_for(conn: sqlite3.Connection, action_hash: str) -> Sequence[Approval]:
    """Every attempt for one proposal, oldest first."""
    rows = conn.execute(
        "SELECT * FROM approvals WHERE action_hash = ? ORDER BY id", (action_hash,)
    ).fetchall()
    return [
        Approval(
            message_id=r["message_id"], username=r["username"], sender_id=r["sender_id"],
            decision=ApprovalKind(r["decision"]), reason=r["reason"], ts_ms=r["ts_ms"],
            counted=bool(r["counted"]),
        )
        for r in rows
    ]  # fmt: skip


# ---------------------------------------------------------------- autopilot rules (CS-F20)


class PaidRow(Frozen):
    """One paid ledger row with the account it went to (for autopilot eligibility)."""

    payment: PaidPayment
    account_number: str


def payment_history(conn: sqlite3.Connection, vendor_id: str) -> list[PaidRow]:
    """Paid rows for a vendor, oldest first; clean = no flags on the proposal and paid by quorum."""
    rows = conn.execute(
        "SELECT l.vendor_id, l.amount_cents, l.ts_ms, l.via, p.flags_json, p.canonical_json "
        "FROM ledger l JOIN proposals p ON p.action_hash = l.action_hash "
        "WHERE l.vendor_id = ? ORDER BY l.id",
        (vendor_id,),
    ).fetchall()
    out = []
    for r in rows:
        account = str(json.loads(r["canonical_json"])["bank"]["account_number"])
        out.append(
            PaidRow(
                payment=PaidPayment(
                    vendor_id=r["vendor_id"], amount_cents=r["amount_cents"],
                    account_hash=account_hash(account), clean=json.loads(r["flags_json"]) == [],
                    via=r["via"], ts_ms=r["ts_ms"],
                ),
                account_number=account,
            )
        )  # fmt: skip
    return out


def last_revoked_ms(conn: sqlite3.Connection, vendor_id: str) -> int | None:
    """Time of the vendor's latest AUTOPILOT_REVOKED receipt (counters restart after it)."""
    rows = conn.execute(
        "SELECT body_json, ts_ms FROM receipts WHERE type = 'AUTOPILOT_REVOKED' ORDER BY seq"
    ).fetchall()
    times = [r["ts_ms"] for r in rows if json.loads(r["body_json"]).get("vendor_id") == vendor_id]
    return max(times) if times else None


def insert_rule(conn: sqlite3.Connection, rule: AutopilotRule, grant_hash: str) -> None:
    conn.execute(
        "INSERT INTO autopilot_rules (rule_id, vendor_id, account_hash, max_cents, cap_cents, "
        "spent_cents, granted_at_ms, expires_at_ms, state, grant_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (rule.rule_id, rule.vendor_id, rule.account_hash, rule.max_cents, rule.cap_cents,
         rule.spent_cents, rule.granted_at_ms, rule.expires_at_ms, rule.state.value, grant_hash),
    )  # fmt: skip


def _rule(row: sqlite3.Row | None) -> AutopilotRule | None:
    if row is None:
        return None
    return AutopilotRule.model_validate({k: row[k] for k in AutopilotRule.model_fields})


def get_rule(conn: sqlite3.Connection, rule_id: str) -> AutopilotRule | None:
    """A rule by id (case-insensitive)."""
    row = conn.execute(
        "SELECT * FROM autopilot_rules WHERE lower(rule_id) = lower(?)", (rule_id,)
    ).fetchone()
    return _rule(row)


def rules(conn: sqlite3.Connection) -> list[AutopilotRule]:
    """Every rule, oldest first."""
    rows = conn.execute("SELECT * FROM autopilot_rules ORDER BY granted_at_ms, rule_id").fetchall()
    return [r for row in rows if (r := _rule(row)) is not None]


def active_rule(conn: sqlite3.Connection, vendor_id: str) -> AutopilotRule | None:
    """The vendor's ACTIVE rule, if any."""
    row = conn.execute(
        "SELECT * FROM autopilot_rules WHERE vendor_id = ? AND state = 'ACTIVE'", (vendor_id,)
    ).fetchone()
    return _rule(row)


def activate_rule(conn: sqlite3.Connection, rule_id: str, granted_ms: int, expires_ms: int) -> None:
    conn.execute(
        "UPDATE autopilot_rules SET state = 'ACTIVE', granted_at_ms = ?, expires_at_ms = ? "
        "WHERE rule_id = ?",
        (granted_ms, expires_ms, rule_id),
    )


def close_rule(conn: sqlite3.Connection, rule_id: str, state: AutopilotState, reason: str) -> None:
    conn.execute(
        "UPDATE autopilot_rules SET state = ?, revoke_reason = ? WHERE rule_id = ?",
        (state.value, reason, rule_id),
    )


def add_spent(conn: sqlite3.Connection, rule_id: str, cents: int) -> None:
    conn.execute(
        "UPDATE autopilot_rules SET spent_cents = spent_cents + ? WHERE rule_id = ?",
        (cents, rule_id),
    )
