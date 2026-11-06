"""End-to-end handler flow with a fake Switch: propose -> approve -> PAID -> receipts (G1 shape)."""

from __future__ import annotations

import pytest

from countersign import proposals
from countersign.app import dispatch, handle_event, sweep_expired
from countersign.core.addressing import Rule
from countersign.core.models import ProposalState
from countersign.runtime import Runtime, StartupError
from countersign.store import ledger, receipts, repo
from countersign.switch.models import RoomSummary
from tests.app.conftest import APPROVALS, AUDIT, INTAKE, FakeOps, event, propose_body
from tests.conftest import T0


async def open_card(rt: Runtime, ops: FakeOps, **over: object) -> str:
    await dispatch(rt, event(INTAKE, "intake", propose_body(**over), T0))
    (row,) = repo.list_open(rt.conn)
    return row.short_id


async def test_inv_1040_end_to_end(rt: Runtime, ops: FakeOps) -> None:
    sid = await open_card(rt, ops)
    assert any("CS-" in b for b in ops.said(APPROVALS))
    await dispatch(rt, event(APPROVALS, "kabir", f"@countersign approve {sid}", T0 + 5000))
    row = repo.get_by_short_id(rt.conn, sid)
    assert row is not None and row.state is ProposalState.PAID
    assert ledger.total_cents(rt.conn) == 48000
    assert receipts.verify(rt.conn).ok
    assert any("PAID" in b for b in ops.said(AUDIT))


async def test_initiator_cannot_approve_own(rt: Runtime, ops: FakeOps) -> None:
    ops.history[0]["sender_name"] = "kabir"
    sid = await open_card(rt, ops)
    await dispatch(rt, event(APPROVALS, "kabir", f"@countersign approve {sid}", T0 + 5000))
    assert ledger.total_cents(rt.conn) == 0
    row = repo.get_by_short_id(rt.conn, sid)
    assert row is not None and row.state is ProposalState.OPEN


async def test_non_approver_not_counted(rt: Runtime, ops: FakeOps) -> None:
    sid = await open_card(rt, ops)
    await dispatch(rt, event(APPROVALS, "riya", f"@countersign approve {sid}", T0 + 5000))
    assert ledger.total_cents(rt.conn) == 0
    assert "not counted" in ops.said(APPROVALS)[-1].lower() or "✗" in ops.said(APPROVALS)[-1]


async def test_reject_blocks_payment(rt: Runtime, ops: FakeOps) -> None:
    sid = await open_card(rt, ops)
    await dispatch(rt, event(APPROVALS, "meera", f"@countersign reject {sid} wrong amount", T0 + 1))
    await dispatch(rt, event(APPROVALS, "kabir", f"@countersign approve {sid}", T0 + 2))
    row = repo.get_by_short_id(rt.conn, sid)
    assert row is not None and row.state is ProposalState.REJECTED
    assert ledger.total_cents(rt.conn) == 0


async def test_evidence_mismatch_refused(rt: Runtime, ops: FakeOps) -> None:
    await dispatch(rt, event(INTAKE, "intake", propose_body(amount_cents=120000), T0))
    assert repo.list_open(rt.conn) == []
    assert any(r.type == "REFUSED" for r in _receipts(rt))


async def test_two_approvals_needed_above_tier(
    rt: Runtime, ops: FakeOps, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(proposals, "extract_text", lambda _b: "INV-1040 1,200.00 7700004412")
    sid = await open_card(rt, ops, amount_cents=120000)
    await dispatch(rt, event(APPROVALS, "kabir", f"@countersign approve {sid}", T0 + 1))
    assert ledger.total_cents(rt.conn) == 0
    await dispatch(rt, event(APPROVALS, "kabir", f"@countersign approve {sid}", T0 + 2))
    assert ledger.total_cents(rt.conn) == 0
    await dispatch(rt, event(APPROVALS, "meera", f"@countersign approve {sid}", T0 + 3))
    assert ledger.total_cents(rt.conn) == 120000
    assert receipts.verify(rt.conn).ok


def _receipts(rt: Runtime) -> list[receipts.Receipt]:
    return [receipts.get(rt.conn, s) for s in range(1, receipts.verify(rt.conn).count + 1)]  # type: ignore[misc]


async def test_unknown_proposer_refused(rt: Runtime, ops: FakeOps) -> None:
    await dispatch(rt, event(INTAKE, "riya", propose_body(), T0))
    assert repo.list_open(rt.conn) == []


async def test_forged_source_message_refused(rt: Runtime, ops: FakeOps) -> None:
    await dispatch(rt, event(INTAKE, "intake", propose_body(source_message_id="nope"), T0))
    assert repo.list_open(rt.conn) == []


async def test_open_addressing_freezes_and_blocks(rt: Runtime, ops: FakeOps) -> None:
    sid = await open_card(rt, ops)
    ops.rules = ()
    await dispatch(rt, event(APPROVALS, "kabir", f"@countersign approve {sid}", T0 + 5000))
    assert ledger.total_cents(rt.conn) == 0
    assert rt.freeze is not None
    assert any("FROZEN" in b for b in ops.said(APPROVALS))


async def test_policy_edit_freezes(rt: Runtime, ops: FakeOps) -> None:
    await rt.guard(T0)
    assert rt.freeze is None
    ops.policy_text = ops.policy_text.replace("below_cents: 100000", "below_cents: 900000")
    assert (await rt.guard(T0 + 1)) is not None


async def test_switch_down_fails_closed(rt: Runtime, ops: FakeOps) -> None:
    ops.fail_reads = True
    assert (await rt.guard(T0)) is not None


async def test_duplicate_event_handled_once(rt: Runtime, ops: FakeOps) -> None:
    sid = await open_card(rt, ops)
    ev = event(APPROVALS, "kabir", f"@countersign approve {sid}", T0 + 5000)
    await handle_event(rt, None, ev)
    n = len(ops.posts)
    await handle_event(rt, None, ev)
    assert len(ops.posts) == n
    assert ledger.total_cents(rt.conn) == 48000


async def test_expiry_sweep(rt: Runtime, ops: FakeOps) -> None:
    sid = await open_card(rt, ops)
    ops.history.append({"id": "later", "kind": "message", "timestamp": T0 + 2_000_000})
    ops_read = ops.read_context

    async def read(
        room_id: str, limit: int = 50, since: str | None = None
    ) -> list[dict[str, object]]:
        return (
            await ops_read(room_id, limit, since)
            if room_id == INTAKE
            else [{"id": "x", "kind": "message", "timestamp": T0 + 2_000_000}]
        )

    ops.read_context = read  # type: ignore[method-assign]
    assert await sweep_expired(rt) == 1
    row = repo.get_by_short_id(rt.conn, sid)
    assert row is not None and row.state is ProposalState.EXPIRED


async def test_late_approval_expires_lazily(rt: Runtime, ops: FakeOps) -> None:
    sid = await open_card(rt, ops)
    await dispatch(rt, event(APPROVALS, "kabir", f"@countersign approve {sid}", T0 + 2_000_000))
    row = repo.get_by_short_id(rt.conn, sid)
    assert row is not None and row.state is ProposalState.EXPIRED
    assert ledger.total_cents(rt.conn) == 0


async def test_duplicate_room_names_stop_startup(rt: Runtime, ops: FakeOps) -> None:
    original = ops.list_all_rooms

    async def twice() -> tuple[RoomSummary, ...]:
        return (*await original(), RoomSummary(room_id="R-DUP", name="ap-approvals"))

    ops.list_all_rooms = twice  # type: ignore[method-assign]
    with pytest.raises(StartupError):
        await rt.locate_rooms()


async def test_approve_in_intake_room_is_wrong_room(rt: Runtime, ops: FakeOps) -> None:
    sid = await open_card(rt, ops)
    await dispatch(rt, event(INTAKE, "intake", f"@countersign approve {sid}", T0 + 5000))
    row = repo.get_by_short_id(rt.conn, sid)
    assert row is not None and row.state is ProposalState.OPEN
    assert ledger.total_cents(rt.conn) == 0
    assert any("E_WRONG_ROOM" in b for b in ops.said(INTAKE))
    assert repo.approvals_for(rt.conn, row.action_hash) == []


async def test_unfreeze_after_restart_is_recorded(rt: Runtime, ops: FakeOps) -> None:
    ops.rules = ()
    await rt.guard(T0)
    assert rt.freeze is not None
    # a new process: same database, no memory of the freeze
    ops.rules = (Rule(rooms=(APPROVALS,), users=("u-meera", "u-arjun", "u-kabir"), agents=()),)
    fresh = Runtime(rt.cfg, rt.conn, rt.ops, rt.poster)
    fresh.rooms = rt.rooms
    assert await fresh.guard(T0 + 1) is None
    assert repo.last_freeze(rt.conn) is not None and repo.last_freeze(rt.conn)[0] == "UNFROZEN"


async def test_quiet_room_expires_from_the_server_clock(rt: Runtime, ops: FakeOps) -> None:
    sid = await open_card(rt, ops)
    assert await sweep_expired(rt) == 0
    ops.server_time_ms = T0 + 2_000_000  # Switch's Date header moved on; no new messages
    assert await sweep_expired(rt) == 1
    row = repo.get_by_short_id(rt.conn, sid)
    assert row is not None and row.state is ProposalState.EXPIRED
