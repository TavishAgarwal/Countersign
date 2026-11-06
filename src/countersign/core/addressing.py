"""Layer-1 addressing check (architecture.md §11.1, threats T11 and T15).

Countersign reads its own addressing policy from Switch and calls check_addressing() at startup,
before every approval decision and payment, and while idle. Any doubt means FROZEN.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from pydantic import BaseModel, ConfigDict

from countersign.core.codes import Code


class Rule(BaseModel):
    """One Switch addressing rule. Switch adds fields over time, so unknown ones are ignored.

    Each of ``rooms``, ``room_groups``, ``users`` and ``agents`` is ``"*"`` (any) or a list.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    rooms: str | tuple[str, ...] = "*"
    room_groups: str | tuple[str, ...] = "*"
    users: str | tuple[str, ...] = "*"
    agents: str | tuple[str, ...] = "*"
    owner: bool = False
    owner_agents: bool = False
    platform: bool = False


def _covers_room(rule: Rule, room_id: str) -> bool:
    return rule.rooms == "*" or (not isinstance(rule.rooms, str) and room_id in rule.rooms)


def check_addressing(
    rules: Sequence[Rule],
    approvals_room_id: str,
    approver_ids: Collection[str],
    admin_id: str | None,
) -> Code | None:
    """Return ``E_ADDRESSING_OPEN`` unless the rules are tight enough for ``#ap-approvals``.

    Inputs: the agent's rules, the approvals room id, the participant ids of the listed
    approvers and the Switch admin's participant id (None if unknown).
    Reason code ``E_ADDRESSING_OPEN`` when: there are no rules (Switch treats that as allow-all);
    a rule covering the approvals room admits agents (``agents`` is ``"*"`` or non-empty), every
    user (``users == "*"``), ``platform`` or ``owner_agents``; or it names a user id that is
    neither a listed approver nor the admin. Returns None when the policy is acceptable.
    """
    if not rules:
        return Code.E_ADDRESSING_OPEN
    allowed = set(approver_ids) | ({admin_id} if admin_id else set())
    for rule in rules:
        if not _covers_room(rule, approvals_room_id):
            continue
        if rule.agents == "*" or rule.agents:
            return Code.E_ADDRESSING_OPEN
        if rule.platform or rule.owner_agents or isinstance(rule.users, str):
            return Code.E_ADDRESSING_OPEN
        if any(u not in allowed for u in rule.users):
            return Code.E_ADDRESSING_OPEN
    return None
