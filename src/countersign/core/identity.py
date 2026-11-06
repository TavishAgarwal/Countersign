"""Layer-2 identity decision (architecture.md §6.3, CS-F10) and proposer check."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from countersign.core.codes import Code
from countersign.core.models import Frozen, Policy


class Participant(Frozen):
    """An entry from ``list_participants``: a name and a type (``user`` or ``agent``)."""

    name: str
    type: str


class IdentityInput(Frozen):
    """Everything identity.py needs; all of it comes from the server event or our own store."""

    room_id: str
    approvals_room_id: str
    sender_kind: str | None
    sender_name: str
    sender_id: str
    sender_role: str | None
    participants: tuple[Participant, ...]


class IdentityOk(Frozen):
    """The sender passed every check. ``pin_needed`` is True on first sight of the username."""

    pin_needed: bool


class IdentityDenied(Frozen):
    """The sender failed a check."""

    code: Code


def check_identity(
    event: IdentityInput, pinned: Mapping[str, str], policy: Policy
) -> IdentityOk | IdentityDenied:
    """Apply the six rules in order; the first failure wins (fail closed).

    1. room is #ap-approvals -> else ``E_WRONG_ROOM``
    2. sender_kind is not "platform" -> else ``E_NOT_HUMAN``
    3. sender_name is on the approver list -> else ``E_NOT_APPROVER``
    4. participants list the name as a user and never as an agent -> an agent entry gives
       ``E_NOT_HUMAN``; no user entry at all gives ``E_NOT_APPROVER``
    5. the pinned sender id matches -> else ``E_IDENTITY_CHANGED``; first sight pins it
    6. if ``require_workspace_role``, sender_role is set -> else ``E_NOT_APPROVER``
    """
    if event.room_id != event.approvals_room_id:
        return IdentityDenied(code=Code.E_WRONG_ROOM)
    if event.sender_kind == "platform":
        return IdentityDenied(code=Code.E_NOT_HUMAN)
    if event.sender_name not in policy.approvers:
        return IdentityDenied(code=Code.E_NOT_APPROVER)
    types = [p.type for p in event.participants if p.name == event.sender_name]
    if "agent" in types:
        return IdentityDenied(code=Code.E_NOT_HUMAN)
    if "user" not in types:
        return IdentityDenied(code=Code.E_NOT_APPROVER)
    known = pinned.get(event.sender_name)
    if known is not None and known != event.sender_id:
        return IdentityDenied(code=Code.E_IDENTITY_CHANGED)
    if policy.require_workspace_role and not event.sender_role:
        return IdentityDenied(code=Code.E_NOT_APPROVER)
    return IdentityOk(pin_needed=known is None)


def check_proposer(sender_name: str, policy: Policy) -> Code | None:
    """Return ``E_NOT_PROPOSER`` unless the sender is a listed proposer agent."""
    return None if sender_name in policy.proposers else Code.E_NOT_PROPOSER


def participants_from(pairs: Sequence[tuple[str, str]]) -> tuple[Participant, ...]:
    """Build participants from ``(name, type)`` pairs (helper for adapters and tests)."""
    return tuple(Participant(name=n, type=t) for n, t in pairs)
