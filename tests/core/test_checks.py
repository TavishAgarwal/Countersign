from __future__ import annotations

import pytest

from countersign.core.checks import baseline_cents, evaluate, median_cents
from countersign.core.codes import Code
from countersign.core.models import FlagCode, Policy, VendorBook
from tests.conftest import make_proposal

ACME_NEW = {"account_number": "7700009087", "routing_number": "021000021"}
NORTH_NEW = {"account_number": "7700008120", "routing_number": "026009593"}


def run(policy: Policy, vendors: VendorBook, history: list[int] | None = None, **over: object):
    return evaluate(make_proposal(**over), vendors, history or [], policy)


def codes_of(res) -> list[FlagCode]:  # type: ignore[no-untyped-def]
    return [f.code for f in res.flags]


def test_inv_1040_clean_k1(policy: Policy, vendors: VendorBook) -> None:
    res = run(policy, vendors)
    assert (res.flags, res.k_required, res.needs_callback, res.refusal) == ((), 1, False, None)


def test_inv_1041_clean_k2(policy: Policy, vendors: VendorBook) -> None:
    res = run(
        policy, vendors, vendor_id="V-NORTH", invoice_number="INV-1041", amount_cents=215000,
        bank={"account_number": "7700007731", "routing_number": "026009593"},
    )  # fmt: skip
    assert (res.flags, res.k_required, res.needs_callback) == ((), 2, False)


def test_inv_1042_bank_change_and_anomaly(policy: Policy, vendors: VendorBook) -> None:
    res = run(policy, vendors, invoice_number="INV-1042", amount_cents=248000, bank=ACME_NEW)
    assert codes_of(res) == [FlagCode.BANK_CHANGED, FlagCode.AMOUNT_ANOMALY]
    assert (res.k_required, res.needs_callback) == (2, True)
    bank, anomaly = res.flags
    assert (bank.old_last4, bank.new_last4, bank.since) == ("4412", "9087", "2025-03-01")
    assert (anomaly.ratio_milli, anomaly.baseline_cents) == (4960, 50000)


def test_inv_1043_anomaly_only(policy: Policy, vendors: VendorBook) -> None:
    res = run(
        policy, vendors, vendor_id="V-GLOBEX", invoice_number="INV-1043", amount_cents=990000,
        bank={"account_number": "7700002290", "routing_number": "011000138"},
    )  # fmt: skip
    assert codes_of(res) == [FlagCode.AMOUNT_ANOMALY]
    assert (res.k_required, res.needs_callback) == (2, False)
    assert res.flags[0].ratio_milli == 3000


def test_inv_1045_unknown_vendor_is_refused(policy: Policy, vendors: VendorBook) -> None:
    res = run(policy, vendors, vendor_id="V-NOPE")
    assert res.refusal is Code.E_UNKNOWN_VENDOR and res.vendor is None


def test_inv_1048_bank_change_after_history(policy: Policy, vendors: VendorBook) -> None:
    res = run(
        policy, vendors, [215000, 220000, 205000], vendor_id="V-NORTH",
        invoice_number="INV-1048", amount_cents=210000, bank=NORTH_NEW,
    )  # fmt: skip
    assert codes_of(res) == [FlagCode.BANK_CHANGED]
    assert (res.k_required, res.needs_callback) == (2, True)


def test_routing_change_alone_is_a_bank_change(policy: Policy, vendors: VendorBook) -> None:
    res = run(policy, vendors, bank={"account_number": "7700004412", "routing_number": "999999999"})
    assert codes_of(res) == [FlagCode.BANK_CHANGED]


@pytest.mark.parametrize(
    ("amount", "k"), [(1, 1), (99_999, 1), (100_000, 2), (100_001, 2), (5_000_000, 2)]
)
def test_tier_boundaries(policy: Policy, vendors: VendorBook, amount: int, k: int) -> None:
    res = run(policy, vendors, vendor_id="V-GLOBEX", amount_cents=amount,
              bank={"account_number": "7700002290", "routing_number": "011000138"})  # fmt: skip
    assert res.k_required == k


@pytest.mark.parametrize(
    ("amount", "flagged"), [(100_000, False), (100_001, True), (99_999, False)]
)
def test_anomaly_threshold_is_strictly_more_than_2x(
    policy: Policy, vendors: VendorBook, amount: int, flagged: bool
) -> None:
    res = run(policy, vendors, amount_cents=amount)  # ACME baseline 50_000
    assert (FlagCode.AMOUNT_ANOMALY in codes_of(res)) is flagged


def test_baseline_switches_to_paid_median_after_two_payments(
    policy: Policy, vendors: VendorBook
) -> None:
    acme = vendors.get("V-ACME")
    assert acme is not None
    assert baseline_cents(acme, []) == 50_000
    assert baseline_cents(acme, [90_000]) == 50_000
    assert baseline_cents(acme, [90_000, 110_000]) == 100_000
    # $2,000 is 4x the file baseline but only 2x the paid median of $1,000 -> not flagged
    res = run(policy, vendors, [100_000, 100_000], amount_cents=200_000)
    assert FlagCode.AMOUNT_ANOMALY not in codes_of(res)
    res = run(policy, vendors, [100_000, 100_000], amount_cents=200_001)
    assert FlagCode.AMOUNT_ANOMALY in codes_of(res)


def test_median() -> None:
    assert median_cents([3, 1, 2]) == 2
    assert median_cents([215000, 220000]) == 217500
    assert median_cents([1, 2]) == 1  # floored, integer only
