"""Deterministic proposal checks: vendor, bank change, amount anomaly, tier, callback (CS-F5)."""

from __future__ import annotations

from collections.abc import Sequence

from countersign.core.codes import Code
from countersign.core.models import (
    Flag,
    FlagCode,
    Frozen,
    Policy,
    Proposal,
    Vendor,
    VendorBook,
    last4,
)


class CheckResult(Frozen):
    """Outcome of evaluate(): flags, quorum size, callback need, or a refusal."""

    vendor: Vendor | None
    flags: tuple[Flag, ...]
    k_required: int
    needs_callback: bool
    refusal: Code | None


def median_cents(values: Sequence[int]) -> int:
    """Integer median; for an even count the mean of the two middle values, floored."""
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) // 2


def baseline_cents(vendor: Vendor, paid_amounts: Sequence[int]) -> int:
    """Vendor baseline: the median of paid history once there are 2+ payments, else the file."""
    return median_cents(paid_amounts) if len(paid_amounts) >= 2 else vendor.baseline_cents


def evaluate(
    proposal: Proposal,
    vendors: VendorBook,
    paid_amounts: Sequence[int],
    policy: Policy,
) -> CheckResult:
    """Evaluate a validated proposal against the vendor file, paid history and policy.

    Inputs: the proposal, the vendor book, amounts (cents) already paid to this vendor and
    the policy. Output: flags, ``k_required``, ``needs_callback``.
    Reason codes: ``E_UNKNOWN_VENDOR`` when the vendor is not in the vendor doc.
    """
    vendor = vendors.get(proposal.vendor_id)
    if vendor is None:
        return CheckResult(
            vendor=None, flags=(), k_required=0, needs_callback=False, refusal=Code.E_UNKNOWN_VENDOR
        )
    flags: list[Flag] = []
    k = policy.tier_k(proposal.amount_cents)
    needs_callback = False

    bank = proposal.bank
    if bank.account_number != vendor.account_number or bank.routing_number != vendor.routing_number:
        flags.append(
            Flag(
                code=FlagCode.BANK_CHANGED,
                old_last4=last4(vendor.account_number),
                new_last4=last4(bank.account_number),
                since=vendor.bank_on_file_since,
            )
        )
        k = max(k, policy.bank_change.k)
        needs_callback = policy.bank_change.callback_required

    base = baseline_cents(vendor, paid_amounts)
    if proposal.amount_cents * 1000 > base * policy.anomaly_factor_milli:
        flags.append(
            Flag(
                code=FlagCode.AMOUNT_ANOMALY,
                ratio_milli=proposal.amount_cents * 1000 // base,
                baseline_cents=base,
            )
        )
    return CheckResult(
        vendor=vendor,
        flags=tuple(flags),
        k_required=k,
        needs_callback=needs_callback,
        refusal=None,
    )
