"""CS-F20 as pure tests: the INV-1041 -> 1048 sequence from architecture.md §8.2."""

from __future__ import annotations

from countersign.core import autopilot as ap
from countersign.core.models import AutopilotRule, AutopilotState, Flag, FlagCode, Policy
from tests.conftest import T0

DAY = 86_400_000
NORTH = ap.account_hash("7700007731")
OTHER = ap.account_hash("7700008120")


def pay(amount: int, ts: int, *, acct: str = NORTH, clean: bool = True, via: str = "quorum"):
    return ap.PaidPayment(
        vendor_id="V-NORTH", amount_cents=amount, account_hash=acct, clean=clean,
        via=via, ts_ms=ts,
    )  # fmt: skip


def proposed(policy: Policy, history: list[ap.PaidPayment], **kw: object):
    return ap.propose_rule("V-NORTH", history, [], policy, T0 + 10 * DAY, **kw)  # type: ignore[arg-type]


def active(spent: int = 0) -> AutopilotRule:
    return AutopilotRule(
        rule_id="AP-41c0d2", vendor_id="V-NORTH", account_hash=NORTH, max_cents=240000,
        cap_cents=1000000, spent_cents=spent, granted_at_ms=T0, expires_at_ms=T0 + 30 * DAY,
        state=AutopilotState.ACTIVE,
    )  # fmt: skip


def test_rule_max_rounds_up_to_100_dollars() -> None:
    assert ap.rule_max_cents(217500, 10) == 240000  # 110% = 2392.50 -> $2,400
    assert ap.rule_max_cents(200000, 10) == 220000  # exactly $2,200, no extra round up
    assert ap.rule_max_cents(200001, 10) == 230000


def test_one_clean_run_is_not_enough(policy: Policy) -> None:
    assert proposed(policy, [pay(215000, T0)]) is None


def test_two_clean_runs_propose_the_documented_rule(policy: Policy) -> None:
    got = proposed(policy, [pay(215000, T0), pay(220000, T0 + DAY)])
    assert got is not None
    assert got.median_cents == 217500 and got.rule.max_cents == 240000
    assert got.rule.cap_cents == 1_000_000 and got.rule.state is AutopilotState.PROPOSED
    assert got.rule.expires_at_ms == T0 + 40 * DAY  # now + 30 days
    assert got.short_id.startswith("AP-") and got.rule.rule_id == got.short_id
    assert got.rule.account_hash == NORTH


def test_proposal_is_deterministic(policy: Policy) -> None:
    h = [pay(215000, T0), pay(220000, T0 + DAY)]
    a, b = proposed(policy, h), proposed(policy, h)
    assert a is not None and b is not None and a.action_hash == b.action_hash


def test_amount_outside_band_blocks_proposal(policy: Policy) -> None:
    assert proposed(policy, [pay(215000, T0), pay(300000, T0 + DAY)]) is None


def test_changed_account_flagged_or_non_quorum_runs_block_proposal(policy: Policy) -> None:
    assert proposed(policy, [pay(215000, T0), pay(215000, T0 + DAY, acct=OTHER)]) is None
    assert proposed(policy, [pay(215000, T0), pay(215000, T0 + DAY, clean=False)]) is None
    assert proposed(policy, [pay(215000, T0), pay(215000, T0 + DAY, via="autopilot")]) is None


def test_only_the_last_runs_count(policy: Policy) -> None:
    old_noise = pay(900000, T0 - DAY, clean=False)
    assert proposed(policy, [old_noise, pay(215000, T0), pay(220000, T0 + DAY)]) is not None


def test_revocation_resets_the_counter(policy: Policy) -> None:
    h = [pay(215000, T0), pay(220000, T0 + DAY)]
    assert proposed(policy, h, last_revoked_ms=T0 + 2 * DAY) is None
    assert proposed(policy, h, last_revoked_ms=T0 - 1) is not None


def test_no_second_rule_while_one_is_open_and_disabled_policy(policy: Policy) -> None:
    h = [pay(215000, T0), pay(220000, T0 + DAY)]
    assert ap.propose_rule("V-NORTH", h, [active()], policy, T0) is None
    pending = active().model_copy(update={"state": AutopilotState.PROPOSED})
    assert ap.propose_rule("V-NORTH", h, [pending], policy, T0) is None
    revoked = active().model_copy(update={"state": AutopilotState.REVOKED})
    assert ap.propose_rule("V-NORTH", h, [revoked], policy, T0) is not None
    off = policy.model_copy(
        update={"autopilot": policy.autopilot.model_copy(update={"enabled": False})}
    )
    assert ap.propose_rule("V-NORTH", h, [], off, T0) is None


def test_inv_1047_is_paid_on_autopilot() -> None:
    res = ap.covers(active(), 205000, "7700007731", [], True, T0 + DAY)
    assert res == ap.Covered(rule_id="AP-41c0d2")


def test_inv_1048_bank_change_revokes() -> None:
    flag = Flag(code=FlagCode.BANK_CHANGED, old_last4="7731", new_last4="8120", since="2025-01-15")
    res = ap.covers(active(), 210000, "7700008120", [flag], True, T0 + DAY)
    assert res == ap.Revoke(
        rule_id="AP-41c0d2", trigger="BANK_CHANGED", new_state=AutopilotState.REVOKED
    )


def test_amount_anomaly_flag_revokes() -> None:
    flag = Flag(code=FlagCode.AMOUNT_ANOMALY, ratio_milli=3000, baseline_cents=215000)
    res = ap.covers(active(), 650000, "7700007731", [flag], True, T0)
    assert isinstance(res, ap.Revoke) and res.trigger == "AMOUNT_ANOMALY"


def test_account_change_without_flag_still_revokes() -> None:
    res = ap.covers(active(), 205000, "7700008120", [], True, T0)
    assert isinstance(res, ap.Revoke) and res.trigger == "BANK_CHANGED"


def test_evidence_mismatch_revokes() -> None:
    res = ap.covers(active(), 205000, "7700007731", [], False, T0)
    assert isinstance(res, ap.Revoke) and res.trigger == "EVIDENCE_MISMATCH"


def test_cap_revokes_but_exact_cap_is_fine() -> None:
    assert isinstance(
        ap.covers(active(spent=800000), 200000, "7700007731", [], True, T0), ap.Covered
    )
    res = ap.covers(active(spent=800001), 200000, "7700007731", [], True, T0)
    assert isinstance(res, ap.Revoke) and res.trigger == "CAP_REACHED"


def test_expiry_expires_the_rule() -> None:
    res = ap.covers(active(), 205000, "7700007731", [], True, T0 + 30 * DAY)
    assert res == ap.Revoke(
        rule_id="AP-41c0d2", trigger="EXPIRED", new_state=AutopilotState.EXPIRED
    )


def test_above_max_needs_full_approvals_but_keeps_the_rule() -> None:
    res = ap.covers(active(), 240001, "7700007731", [], True, T0)
    assert isinstance(res, ap.NotCovered)


def test_inactive_rule_covers_nothing() -> None:
    proposed_rule = active().model_copy(update={"state": AutopilotState.PROPOSED})
    assert isinstance(ap.covers(proposed_rule, 205000, "7700007731", [], True, T0), ap.NotCovered)


def test_manual_revoke_names_the_person() -> None:
    res = ap.revoke_manual(active(), "meera")
    assert res.trigger == "revoked by meera" and res.new_state is AutopilotState.REVOKED


def test_full_inv_1041_to_1048_sequence(policy: Policy) -> None:
    history = [pay(215000, T0)]  # INV-1041
    assert proposed(policy, history) is None
    history.append(pay(220000, T0 + DAY))  # INV-1046
    offer = proposed(policy, history)
    assert offer is not None and offer.rule.max_cents == 240000
    granted = offer.rule.model_copy(
        update={"state": AutopilotState.ACTIVE, "granted_at_ms": T0 + 2 * DAY}
    )
    assert isinstance(
        ap.covers(granted, 205000, "7700007731", [], True, T0 + 3 * DAY), ap.Covered
    )  # INV-1047
    flag = Flag(code=FlagCode.BANK_CHANGED, old_last4="7731", new_last4="8120", since="2025-01-15")
    last = ap.covers(granted, 210000, "7700008120", [flag], True, T0 + 4 * DAY)  # INV-1048
    assert isinstance(last, ap.Revoke) and last.trigger == "BANK_CHANGED"
