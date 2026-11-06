"""Frozen data models for the pure core (architecture.md §10, §7, §8.3).

Our own models forbid extra fields and are immutable (rules.md §2). Money is integer cents.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from countersign.core.codes import Code

MAX_AMOUNT_CENTS = 100_000_000
INVOICE_NUMBER_RE = re.compile(r"^INV-\d{4,}$")
_DIGITS_RE = re.compile(r"^\d+$")


class Frozen(BaseModel):
    """Base class: no unknown fields, immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def normalize_digits(value: str) -> str:
    """Strip spaces and dashes from an account or routing number; digits must remain."""
    cleaned = re.sub(r"[\s-]", "", value)
    if not _DIGITS_RE.match(cleaned):
        raise ValueError("must contain digits only")
    return cleaned


def last4(value: str) -> str:
    """Return the last four characters (the only part of an account ever shown)."""
    return value[-4:]


def _iso_date(value: object) -> str:
    """Accept an ISO date string or a YAML date and return ``YYYY-MM-DD``."""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        date.fromisoformat(value)
        return value
    raise ValueError("must be an ISO date YYYY-MM-DD")


# ---------------------------------------------------------------- proposal (§10)


class Bank(Frozen):
    """Bank details on an invoice, digits only."""

    account_number: str
    routing_number: str

    @field_validator("account_number", "routing_number")
    @classmethod
    def _digits(cls, v: str) -> str:
        return normalize_digits(v)


class Attachment(Frozen):
    """The PDF carried by the source message."""

    filename: str = Field(min_length=1)
    mxc: str = Field(min_length=1)


class IntakeFlag(Frozen):
    """Advisory flag from the intake agent; display only."""

    code: str = Field(max_length=64)
    detail: str = Field(max_length=280)


class Proposal(Frozen):
    """A payment proposal from a proposer agent (architecture.md §10).

    ``intake_flags`` and ``intake_note`` are advisory and excluded from the action hash.
    """

    type: Literal["payment_proposal"]
    schema_version: Literal[1] = Field(alias="schema")
    source_message_id: str = Field(min_length=1)
    attachment: Attachment
    vendor_id: str = Field(min_length=1)
    invoice_number: str
    invoice_date: str
    due_date: str
    amount_cents: Annotated[StrictInt, Field(gt=0, le=MAX_AMOUNT_CENTS)]
    currency: Literal["USD"]
    bank: Bank
    intake_flags: tuple[IntakeFlag, ...] = ()
    intake_note: str = Field(default="", max_length=280)

    @field_validator("invoice_number")
    @classmethod
    def _invoice_number(cls, v: str) -> str:
        if not INVOICE_NUMBER_RE.match(v):
            raise ValueError("must match ^INV-\\d{4,}$")
        return v

    @field_validator("invoice_date", "due_date", mode="before")
    @classmethod
    def _dates(cls, v: object) -> str:
        return _iso_date(v)


# ---------------------------------------------------------------- vendors (§8.1)


class Vendor(Frozen):
    """A vendor in vendors.yaml. Account numbers are synthetic test data."""

    vendor_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    account_number: str
    routing_number: str
    phone: str = Field(min_length=1)
    baseline_cents: Annotated[StrictInt, Field(gt=0)]
    bank_on_file_since: str

    @field_validator("account_number", "routing_number")
    @classmethod
    def _digits(cls, v: str) -> str:
        return normalize_digits(v)

    @field_validator("bank_on_file_since", mode="before")
    @classmethod
    def _since(cls, v: object) -> str:
        return _iso_date(v)


class VendorBook(Frozen):
    """The parsed vendors.yaml document."""

    vendors: tuple[Vendor, ...]

    @model_validator(mode="after")
    def _unique(self) -> Self:
        ids = [v.vendor_id for v in self.vendors]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate vendor_id")
        return self

    def get(self, vendor_id: str) -> Vendor | None:
        """Return the vendor with this id, or None."""
        for vendor in self.vendors:
            if vendor.vendor_id == vendor_id:
                return vendor
        return None


# ---------------------------------------------------------------- policy (§8.3)


class Tier(Frozen):
    """Amounts strictly below ``below_cents`` need ``k`` approvals; None = no upper bound."""

    below_cents: Annotated[StrictInt, Field(gt=0)] | None
    k: Annotated[StrictInt, Field(ge=1)]


class BankChangePolicy(Frozen):
    """Escalation when the invoice account differs from the vendor file."""

    k: Annotated[StrictInt, Field(ge=1)]
    callback_required: bool


class AutopilotPolicy(Frozen):
    """Bounds for earned autopilot (CS-F20)."""

    enabled: bool
    clean_runs: Annotated[StrictInt, Field(ge=1)]
    band_pct: Annotated[StrictInt, Field(ge=0, le=100)]
    max_days: Annotated[StrictInt, Field(ge=1)]
    cap_cents: Annotated[StrictInt, Field(gt=0)]
    grant_k: Annotated[StrictInt, Field(ge=1)]


class Policy(Frozen):
    """The parsed policy.yaml document."""

    approvers: tuple[str, ...]
    proposers: tuple[str, ...]
    window_sec: Annotated[StrictInt, Field(gt=0)]
    vendor_window_sec: dict[str, Annotated[StrictInt, Field(gt=0)]] = Field(default_factory=dict)
    tiers: tuple[Tier, ...]
    bank_change: BankChangePolicy
    amount_anomaly_factor: int | float | str
    autopilot: AutopilotPolicy
    policy_change_k: Annotated[StrictInt, Field(ge=1)]
    require_workspace_role: bool = False

    @field_validator("approvers", "proposers")
    @classmethod
    def _names(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        if not v or any(not n or n != n.strip() for n in v) or len(set(v)) != len(v):
            raise ValueError("must be a non-empty list of unique, trimmed usernames")
        return v

    @field_validator("amount_anomaly_factor")
    @classmethod
    def _factor(cls, v: int | float | str) -> int | float | str:
        if isinstance(v, bool) or _factor_milli(v) <= 0:
            raise ValueError("must be a positive number with at most 3 decimals")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not self.tiers or self.tiers[-1].below_cents is not None:
            raise ValueError("tiers must end with an open-ended tier (below_cents: null)")
        bounds = [t.below_cents for t in self.tiers[:-1]]
        if any(b is None for b in bounds) or bounds != sorted(b for b in bounds if b is not None):
            raise ValueError("tier bounds must be ascending")
        biggest = len(self.approvers)
        ks = [t.k for t in self.tiers] + [
            self.bank_change.k,
            self.autopilot.grant_k,
            self.policy_change_k,
        ]
        if any(k > biggest for k in ks):
            raise ValueError("a quorum k exceeds the number of approvers")
        return self

    def window_ms(self, vendor_id: str) -> int:
        """Approval window in milliseconds for this vendor."""
        return self.vendor_window_sec.get(vendor_id, self.window_sec) * 1000

    def tier_k(self, amount_cents: int) -> int:
        """Approvals needed for an amount under the tier table."""
        for tier in self.tiers:
            if tier.below_cents is None or amount_cents < tier.below_cents:
                return tier.k
        return self.tiers[-1].k  # unreachable: last tier is open-ended

    @property
    def anomaly_factor_milli(self) -> int:
        """The anomaly factor times 1000, as an integer (no floats in decisions)."""
        return _factor_milli(self.amount_anomaly_factor)


def _factor_milli(value: int | float | str) -> int:
    """Convert a factor such as 2.0 to 2000; return 0 when not exactly representable."""
    try:
        scaled = Decimal(str(value)) * 1000
    except InvalidOperation:
        return 0
    return int(scaled) if scaled == scaled.to_integral_value() else 0


# ---------------------------------------------------------------- flags and records


class FlagCode(StrEnum):
    """Warning flags shown on cards (design.md §2)."""

    BANK_CHANGED = "BANK_CHANGED"
    AMOUNT_ANOMALY = "AMOUNT_ANOMALY"
    AUTOPILOT_REVOKES = "AUTOPILOT_REVOKES"


class Flag(Frozen):
    """A flag computed by Countersign itself. Structured; chat wording lives in render.py."""

    code: FlagCode
    old_last4: str | None = None
    new_last4: str | None = None
    since: str | None = None
    ratio_milli: int | None = None
    baseline_cents: int | None = None
    rule_id: str | None = None


class ProposalKind(StrEnum):
    """What a quorum is deciding."""

    PAYMENT = "payment"
    AUTOPILOT_GRANT = "autopilot_grant"
    POLICY_RATIFY = "policy_ratify"


class ProposalState(StrEnum):
    """Lifecycle of a proposal row (architecture.md §7)."""

    OPEN = "OPEN"
    PAID = "PAID"
    REFUSED = "REFUSED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    VOID = "VOID"
    GRANTED = "GRANTED"


class ProposalRecord(Frozen):
    """The stored facts about a proposal that the quorum needs. All server-derived."""

    action_hash: str
    short_id: str
    kind: ProposalKind
    initiator: str
    k_required: int
    needs_callback: bool
    state: ProposalState
    opened_at_ms: int
    expires_at_ms: int
    superseded_by: str | None = None


class ApprovalKind(StrEnum):
    """What an approver typed."""

    APPROVE = "approve"
    REJECT = "reject"
    CALLBACK = "callback"


class Approval(Frozen):
    """One attempt that reached Countersign (architecture.md §7 ``approvals``)."""

    message_id: str
    username: str
    sender_id: str
    decision: ApprovalKind
    reason: str | None = None
    ts_ms: int
    counted: bool


class Allow(Frozen):
    """Quorum reached; the action may execute."""


class Pending(Frozen):
    """Not enough yet: ``n`` distinct approvals of ``k`` so far."""

    n: int
    k: int
    callback_missing: bool


class Rejected(Frozen):
    """An approver rejected the action."""

    by: str
    reason: str


class Deny(Frozen):
    """The attempt or action is refused with a reason code."""

    code: Code


Decision = Allow | Pending | Rejected | Deny


class AutopilotState(StrEnum):
    """Lifecycle of an autopilot rule."""

    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class AutopilotRule(Frozen):
    """A quorum-granted, capped, expiring permission to pay one vendor (architecture.md §7)."""

    rule_id: str
    vendor_id: str
    account_hash: str
    max_cents: int
    cap_cents: int
    spent_cents: int = 0
    granted_at_ms: int | None = None
    expires_at_ms: int
    state: AutopilotState
