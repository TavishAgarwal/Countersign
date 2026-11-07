"""``accept-policy``: a policy freeze, the owner's diff and confirmation, and the receipt."""

from __future__ import annotations

from countersign.accept import accept_policy
from countersign.runtime import Runtime
from countersign.store import receipts, repo
from tests.app.conftest import FakeOps
from tests.conftest import T0


async def frozen_by_edit(rt: Runtime, ops: FakeOps) -> None:
    await rt.guard(T0)  # first start: bootstrap ratifies the shipped documents
    ops.policy_text = ops.policy_text.replace("window_sec: 900", "window_sec: 600")
    assert (await rt.guard(T0 + 1)) is not None


async def test_accepting_a_change_unfreezes_and_records_it(rt: Runtime, ops: FakeOps) -> None:
    await frozen_by_edit(rt, ops)
    old = repo.meta_get(rt.conn, "ratified_policy_hash")
    lines: list[str] = []
    code = await accept_policy(rt, lambda _q: True, lines.append)
    assert code == 0
    assert repo.meta_get(rt.conn, "ratified_policy_hash") != old
    assert any("window_sec: 600" in ln for ln in lines)  # the diff showed the change
    assert receipts.verify(rt.conn).ok
    assert any("Policy accepted by the owner" in b for b in ops.said("R-AUD"))
    assert (await rt.guard(T0 + 2)) is None  # payments resume on the next check


async def test_declining_changes_nothing(rt: Runtime, ops: FakeOps) -> None:
    await frozen_by_edit(rt, ops)
    old = repo.meta_get(rt.conn, "ratified_policy_hash")
    assert await accept_policy(rt, lambda _q: False, lambda _s: None) == 1
    assert repo.meta_get(rt.conn, "ratified_policy_hash") == old
    assert (await rt.guard(T0 + 2)) is not None


async def test_nothing_to_accept_when_documents_match(rt: Runtime, ops: FakeOps) -> None:
    await rt.guard(T0)
    lines: list[str] = []
    assert await accept_policy(rt, lambda _q: True, lines.append) == 1
    assert "nothing to accept" in lines[0]


async def test_unparseable_documents_are_never_accepted(rt: Runtime, ops: FakeOps) -> None:
    await rt.guard(T0)
    ops.policy_text = "approvers: [unterminated"
    old = repo.meta_get(rt.conn, "ratified_policy_hash")
    assert await accept_policy(rt, lambda _q: True, lambda _s: None) == 1
    assert repo.meta_get(rt.conn, "ratified_policy_hash") == old
