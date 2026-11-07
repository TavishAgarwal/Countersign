"""Earned autopilot end to end with a fake Switch: offer, grant, use, revoke (CS-F20)."""

from __future__ import annotations

import pytest

from countersign import proposals
from countersign.app import dispatch
from countersign.core.models import AutopilotState, ProposalKind, ProposalState
from countersign.runtime import Runtime
from countersign.store import ledger, receipts, repo
from tests.app.conftest import APPROVALS, AUDIT, INTAKE, FakeOps, event, propose_body
from tests.conftest import T0

NORTH = {"account_number": "7700007731", "routing_number": "026009593"}
MOVED = {"account_number": "7700008120", "routing_number": "026009593"}


def pdf(mp: pytest.MonkeyPatch, number: str, cents: int, account: str) -> None:
    text = f"{number} {cents // 100:,}.{cents % 100:02d} {account}"
    mp.setattr(proposals, "extract_text", lambda _b: text)


async def propose(rt: Runtime, mp: pytest.MonkeyPatch, number: str, cents: int, t: int,
                  bank: dict[str, str] = NORTH) -> None:  # fmt: skip
    pdf(mp, number, cents, bank["account_number"])
    body = propose_body(vendor_id="V-NORTH", invoice_number=number, amount_cents=cents, bank=bank)
    await dispatch(rt, event(INTAKE, "intake", body, t))


def short(rt: Runtime, number: str) -> str:
    row = next(r for r in repo.list_all(rt.conn) if r.invoice_number == number)
    return row.short_id


async def pay(rt: Runtime, mp: pytest.MonkeyPatch, number: str, cents: int, t: int) -> None:
    await propose(rt, mp, number, cents, t)
    sid = short(rt, number)
    await dispatch(rt, event(APPROVALS, "meera", f"@countersign approve {sid}", t + 1))
    await dispatch(rt, event(APPROVALS, "arjun", f"@countersign approve {sid}", t + 2))


def grant_row(rt: Runtime) -> repo.ProposalRow:
    return next(r for r in repo.list_all(rt.conn) if r.kind is ProposalKind.AUTOPILOT_GRANT)


async def active_rule(rt: Runtime, mp: pytest.MonkeyPatch) -> str:
    """Two clean quorum payments, then the grant approved by meera and arjun."""
    await pay(rt, mp, "INV-1041", 215000, T0)
    await pay(rt, mp, "INV-1046", 220000, T0 + 10_000)
    sid = grant_row(rt).short_id
    await dispatch(rt, event(APPROVALS, "meera", f"@countersign approve {sid}", T0 + 20_000))
    await dispatch(rt, event(APPROVALS, "arjun", f"@countersign approve {sid}", T0 + 21_000))
    return sid


async def test_rule_is_offered_after_two_clean_payments(
    rt: Runtime, ops: FakeOps, monkeypatch
) -> None:
    await pay(rt, monkeypatch, "INV-1041", 215000, T0)
    assert not [r for r in repo.list_all(rt.conn) if r.kind is ProposalKind.AUTOPILOT_GRANT]
    await pay(rt, monkeypatch, "INV-1046", 220000, T0 + 10_000)
    row = grant_row(rt)
    assert row.short_id.startswith("AP-") and row.k_required == 2 and not row.needs_callback
    assert any("AUTOPILOT PROPOSED" in b for b in ops.said(APPROVALS))
    assert repo.get_rule(rt.conn, row.short_id).state is AutopilotState.PROPOSED  # type: ignore[union-attr]


async def test_grant_needs_the_quorum_then_pays_on_autopilot(
    rt: Runtime, ops: FakeOps, monkeypatch
) -> None:
    sid = await active_rule(rt, monkeypatch)
    rule = repo.get_rule(rt.conn, sid)
    assert rule is not None and rule.state is AutopilotState.ACTIVE and rule.max_cents == 240000
    assert any("AUTOPILOT ACTIVE" in b for b in ops.said(APPROVALS))
    paid_before = ledger.total_cents(rt.conn)
    await propose(rt, monkeypatch, "INV-1047", 205000, T0 + 30_000)
    entries = ledger.entries(rt.conn)
    assert entries[-1].via == "autopilot" and ledger.total_cents(rt.conn) == paid_before + 205000
    assert repo.get_rule(rt.conn, sid).spent_cents == 205000  # type: ignore[union-attr]
    assert any("AUTOPILOT PAID" in b for b in ops.said(INTAKE))
    assert receipts.verify(rt.conn).ok


async def test_one_approval_does_not_grant(rt: Runtime, ops: FakeOps, monkeypatch) -> None:
    await pay(rt, monkeypatch, "INV-1041", 215000, T0)
    await pay(rt, monkeypatch, "INV-1046", 220000, T0 + 10_000)
    sid = grant_row(rt).short_id
    await dispatch(rt, event(APPROVALS, "meera", f"@countersign approve {sid}", T0 + 20_000))
    assert repo.get_rule(rt.conn, sid).state is AutopilotState.PROPOSED  # type: ignore[union-attr]


async def test_bank_change_revokes_and_needs_full_approvals(
    rt: Runtime, ops: FakeOps, monkeypatch
) -> None:
    sid = await active_rule(rt, monkeypatch)
    paid_before = ledger.total_cents(rt.conn)
    await propose(rt, monkeypatch, "INV-1048", 210000, T0 + 30_000, MOVED)
    rule = repo.get_rule(rt.conn, sid)
    assert rule is not None and rule.state is AutopilotState.REVOKED
    row = repo.get_by_short_id(rt.conn, short(rt, "INV-1048"))
    assert row is not None and row.state is ProposalState.OPEN
    assert row.k_required == 2 and row.needs_callback and "AUTOPILOT_REVOKES" in row.flags_json
    assert ledger.total_cents(rt.conn) == paid_before
    assert any("AUTOPILOT REVOKED" in b and "BANK CHANGED" in b for b in ops.said(APPROVALS))


async def test_amount_above_the_maximum_needs_approvals_but_keeps_the_rule(
    rt: Runtime, ops: FakeOps, monkeypatch
) -> None:
    sid = await active_rule(rt, monkeypatch)
    await propose(rt, monkeypatch, "INV-1049", 250000, T0 + 30_000)
    row = repo.get_by_short_id(rt.conn, short(rt, "INV-1049"))
    assert row is not None and row.state is ProposalState.OPEN
    assert repo.get_rule(rt.conn, sid).state is AutopilotState.ACTIVE  # type: ignore[union-attr]


async def test_an_approver_can_revoke_and_a_stranger_cannot(
    rt: Runtime, ops: FakeOps, monkeypatch
) -> None:
    sid = await active_rule(rt, monkeypatch)
    await dispatch(
        rt, event(APPROVALS, "vikram_ceo", f"@countersign autopilot revoke {sid}", T0 + 40_000)
    )
    assert repo.get_rule(rt.conn, sid).state is AutopilotState.ACTIVE  # type: ignore[union-attr]
    await dispatch(
        rt, event(APPROVALS, "arjun", "@countersign autopilot revoke V-NORTH", T0 + 41_000)
    )
    assert repo.get_rule(rt.conn, sid).state is AutopilotState.REVOKED  # type: ignore[union-attr]
    assert any("revoked by arjun" in b for b in ops.said(APPROVALS))
    assert any("AUTOPILOT_REVOKED" in b or "revoked" in b for b in ops.said(AUDIT))
    # the next invoice is a normal card again, and the offer counters have restarted
    await propose(rt, monkeypatch, "INV-1047", 205000, T0 + 50_000)
    assert repo.get_by_short_id(rt.conn, short(rt, "INV-1047")).state is ProposalState.OPEN  # type: ignore[union-attr]


async def test_list_shows_active_rules(rt: Runtime, ops: FakeOps, monkeypatch) -> None:
    await active_rule(rt, monkeypatch)
    await dispatch(rt, event(APPROVALS, "kabir", "@countersign autopilot list", T0 + 40_000))
    assert "AUTOPILOT ACTIVE" in ops.said(APPROVALS)[-1]


async def test_a_rejected_grant_leaves_no_rule(rt: Runtime, ops: FakeOps, monkeypatch) -> None:
    await pay(rt, monkeypatch, "INV-1041", 215000, T0)
    await pay(rt, monkeypatch, "INV-1046", 220000, T0 + 10_000)
    sid = grant_row(rt).short_id
    await dispatch(rt, event(APPROVALS, "meera", f"@countersign reject {sid} not yet", T0 + 20_000))
    assert repo.get_rule(rt.conn, sid).state is AutopilotState.EXPIRED  # type: ignore[union-attr]


async def test_frozen_countersign_never_pays_on_autopilot(
    rt: Runtime, ops: FakeOps, monkeypatch
) -> None:
    await active_rule(rt, monkeypatch)
    ops.rules = ()  # addressing opened
    before = ledger.total_cents(rt.conn)
    await propose(rt, monkeypatch, "INV-1047", 205000, T0 + 30_000)
    assert ledger.total_cents(rt.conn) == before


async def test_cap_stops_the_rule(rt: Runtime, ops: FakeOps, monkeypatch) -> None:
    sid = await active_rule(rt, monkeypatch)
    rule = repo.get_rule(rt.conn, sid)
    assert rule is not None
    with_cap = rule.cap_cents - 100_000  # leave less room than an invoice
    rt.conn.execute("UPDATE autopilot_rules SET spent_cents = ? WHERE rule_id = ?", (with_cap, sid))
    await propose(rt, monkeypatch, "INV-1047", 205000, T0 + 30_000)
    assert repo.get_rule(rt.conn, sid).state is AutopilotState.REVOKED  # type: ignore[union-attr]
