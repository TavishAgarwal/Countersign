"""Reason codes (architecture.md §9) and their plain-language text (design.md §5-6).

Every refusal carries one of these codes and is visible in chat (rules.md golden rule 10).
"""

from __future__ import annotations

from enum import StrEnum


class Code(StrEnum):
    """A layer-2 reason code. Layer-1 refusals are written by Switch and have no code here."""

    E_BAD_PROPOSAL = "E_BAD_PROPOSAL"
    """Proposal block missing or fails the schema."""
    E_NOT_PROPOSER = "E_NOT_PROPOSER"
    """Proposal not from a listed proposer agent."""
    E_NO_SOURCE = "E_NO_SOURCE"
    """source_message_id not found in #ap-intake."""
    E_UNKNOWN_VENDOR = "E_UNKNOWN_VENDOR"
    """Vendor not in the vendor doc."""
    E_EVIDENCE_MISMATCH = "E_EVIDENCE_MISMATCH"
    """Invoice number, amount or account not found in the PDF text."""
    E_PDF_UNREADABLE = "E_PDF_UNREADABLE"
    """Download or text extraction failed."""
    E_WRONG_ROOM = "E_WRONG_ROOM"
    """Approver command sent outside #ap-approvals."""
    E_NOT_APPROVER = "E_NOT_APPROVER"
    """Username not on the approver list."""
    E_NOT_HUMAN = "E_NOT_HUMAN"
    """Name resolves to an agent in list_participants."""
    E_IDENTITY_CHANGED = "E_IDENTITY_CHANGED"
    """Sender id differs from the pinned id for that username."""
    E_UNKNOWN_ID = "E_UNKNOWN_ID"
    """No proposal with that short id."""
    E_NOT_OPEN = "E_NOT_OPEN"
    """Proposal already PAID, REJECTED or REFUSED."""
    E_SUPERSEDED = "E_SUPERSEDED"
    """Proposal replaced by a newer one."""
    E_EXPIRED = "E_EXPIRED"
    """Outside the approval window."""
    E_INITIATOR = "E_INITIATOR"
    """Sender uploaded the invoice."""
    E_DUPLICATE = "E_DUPLICATE"
    """Sender already approved."""
    E_CALLBACK_REQUIRED = "E_CALLBACK_REQUIRED"
    """Quorum reached but the bank change is not callback-verified."""
    E_REASON_REQUIRED = "E_REASON_REQUIRED"
    """A reject without a reason."""
    E_FROZEN = "E_FROZEN"
    """Payments are frozen (see the banner)."""
    E_POLICY_UNRATIFIED = "E_POLICY_UNRATIFIED"
    """Policy or vendor doc changed and not ratified."""
    E_ADDRESSING_OPEN = "E_ADDRESSING_OPEN"
    """Countersign's addressing policy is looser than required."""
    E_SELF_CHECK = "E_SELF_CHECK"
    """Another startup check failed (rooms, docs, database)."""
    E_BAD_COMMAND = "E_BAD_COMMAND"
    """Message to Countersign is not a known command."""
    E_INTERNAL = "E_INTERNAL"
    """Unexpected error; nothing changed."""


# design.md §5 and §6. Placeholders in {braces} are filled by plain_reason().
_PLAIN: dict[Code, str] = {
    Code.E_INITIATOR: "you uploaded this invoice, so your approval can't count",
    Code.E_DUPLICATE: "you already approved this payment",
    Code.E_NOT_APPROVER: "you're not on the approver list in policy.yaml",
    Code.E_NOT_HUMAN: "this name belongs to an agent; agents can't approve",
    Code.E_IDENTITY_CHANGED: "this account doesn't match the one first seen for {username}",
    Code.E_EXPIRED: "the approval window closed at {time}",
    Code.E_NOT_OPEN: "this payment is already {state}",
    Code.E_SUPERSEDED: "this payment was replaced by {new_id}",
    Code.E_CALLBACK_REQUIRED: (
        "approvals are complete, but the bank change still needs a callback"
    ),
    Code.E_UNKNOWN_ID: "there's no payment {id}",
    Code.E_REASON_REQUIRED: "add a reason: `@countersign reject {id} <reason>`",
    Code.E_WRONG_ROOM: "approvals only count in #ap-approvals",
    Code.E_FROZEN: "payments are paused; see the 🧊 FROZEN banner in #ap-approvals",
    Code.E_POLICY_UNRATIFIED: "{docs} changed since it was last ratified",
    Code.E_ADDRESSING_OPEN: (
        "Countersign's Switch addressing rules are looser than required "
        "(agents or unlisted people could reach it in #ap-approvals)"
    ),
    Code.E_SELF_CHECK: "a startup check failed: {check}",
    Code.E_INTERNAL: "something went wrong on my side; nothing was approved or paid",
    Code.E_BAD_COMMAND: "I didn't understand that. Try @countersign help.",
    Code.E_UNKNOWN_VENDOR: "vendor {name} isn't in vendors.yaml; add it there first",
    Code.E_EVIDENCE_MISMATCH: "the PDF doesn't show the proposed {field}",
    Code.E_PDF_UNREADABLE: "couldn't read text from the PDF",
    Code.E_BAD_PROPOSAL: "the proposal block is missing or invalid: {error}",
    Code.E_NOT_PROPOSER: "only listed proposer agents can propose payments",
    Code.E_NO_SOURCE: "couldn't find the message that carried this invoice",
}


# Placeholders that have a sensible default when the caller does not know better.
_DEFAULTS: dict[Code, dict[str, str]] = {
    Code.E_POLICY_UNRATIFIED: {"docs": "policy.yaml or vendors.yaml"},
}


def plain_reason(code: Code, **fields: str) -> str:
    """Return the plain-language reason for ``code`` with ``{placeholders}`` filled.

    Inputs: a code and the placeholder values it needs (username, time, state, new_id, id,
    check, name, field, error, docs). A missing placeholder is left as written, never an error.
    Output: one sentence, no trailing code. Never raises.
    """
    text = _PLAIN[code]
    for key, value in {**_DEFAULTS.get(code, {}), **fields}.items():
        text = text.replace("{" + key + "}", value)
    return text
