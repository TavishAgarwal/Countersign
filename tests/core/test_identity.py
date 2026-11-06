from __future__ import annotations

import pytest

from countersign.core.codes import Code
from countersign.core.identity import (
    IdentityDenied,
    IdentityInput,
    IdentityOk,
    check_identity,
    check_proposer,
    participants_from,
)
from countersign.core.models import Policy

ROOM = "room-approvals"
PEOPLE = participants_from(
    [("meera", "user"), ("arjun", "user"), ("kabir", "user"), ("vikram_ceo", "user"),
     ("intake", "agent"), ("rogue", "agent")]
)  # fmt: skip


def ev(**over: object) -> IdentityInput:
    raw: dict[str, object] = {
        "room_id": ROOM,
        "approvals_room_id": ROOM,
        "sender_kind": None,
        "sender_name": "meera",
        "sender_id": "sid-meera",
        "sender_role": None,
        "participants": PEOPLE,
    }
    raw.update(over)
    return IdentityInput.model_validate(raw)


def code(res: IdentityOk | IdentityDenied) -> Code:
    assert isinstance(res, IdentityDenied)
    return res.code


def test_first_sight_pins(policy: Policy) -> None:
    res = check_identity(ev(), {}, policy)
    assert res == IdentityOk(pin_needed=True)


def test_known_matching_id_is_ok_without_pin(policy: Policy) -> None:
    assert check_identity(ev(), {"meera": "sid-meera"}, policy) == IdentityOk(pin_needed=False)


def test_wrong_room(policy: Policy) -> None:
    assert code(check_identity(ev(room_id="room-intake"), {}, policy)) is Code.E_WRONG_ROOM


def test_wrong_room_wins_over_everything_else(policy: Policy) -> None:
    res = check_identity(ev(room_id="room-intake", sender_name="rogue"), {}, policy)
    assert code(res) is Code.E_WRONG_ROOM


def test_platform_sender_is_not_human(policy: Policy) -> None:
    assert code(check_identity(ev(sender_kind="platform"), {}, policy)) is Code.E_NOT_HUMAN


@pytest.mark.parametrize("name", ["riya", "vikram_ceo", "Meera", "meera ", "arjun mehta"])
def test_not_on_approver_list(policy: Policy, name: str) -> None:
    assert code(check_identity(ev(sender_name=name), {}, policy)) is Code.E_NOT_APPROVER


def test_display_name_lookalike_is_not_an_approver(policy: Policy) -> None:
    # T13: "Arjun Mehta " is a display name; identity uses the username only
    res = check_identity(ev(sender_name="Arjun Mehta "), {}, policy)
    assert code(res) is Code.E_NOT_APPROVER


def test_listed_name_that_is_an_agent_in_the_room(policy: Policy) -> None:
    people = participants_from([("meera", "agent")])
    assert code(check_identity(ev(participants=people), {}, policy)) is Code.E_NOT_HUMAN


def test_agent_entry_wins_even_if_a_user_entry_also_exists(policy: Policy) -> None:
    people = participants_from([("meera", "user"), ("meera", "agent")])
    assert code(check_identity(ev(participants=people), {}, policy)) is Code.E_NOT_HUMAN


def test_listed_name_missing_from_participants_fails_closed(policy: Policy) -> None:
    people = participants_from([("arjun", "user")])
    assert code(check_identity(ev(participants=people), {}, policy)) is Code.E_NOT_APPROVER


def test_empty_participants_fails_closed(policy: Policy) -> None:
    assert code(check_identity(ev(participants=()), {}, policy)) is Code.E_NOT_APPROVER


def test_pinned_id_mismatch(policy: Policy) -> None:
    res = check_identity(ev(sender_id="sid-other"), {"meera": "sid-meera"}, policy)
    assert code(res) is Code.E_IDENTITY_CHANGED


def test_other_users_pin_does_not_matter(policy: Policy) -> None:
    assert isinstance(check_identity(ev(), {"arjun": "x"}, policy), IdentityOk)


def test_workspace_role_required_when_enabled(policy: Policy) -> None:
    strict = policy.model_copy(update={"require_workspace_role": True})
    assert code(check_identity(ev(sender_role=None), {}, strict)) is Code.E_NOT_APPROVER
    assert isinstance(check_identity(ev(sender_role="member"), {}, strict), IdentityOk)
    assert isinstance(check_identity(ev(sender_role=None), {}, policy), IdentityOk)


def test_check_proposer(policy: Policy) -> None:
    assert check_proposer("intake", policy) is None
    assert check_proposer("scripted-intake", policy) is None
    assert check_proposer("rogue", policy) is Code.E_NOT_PROPOSER
    assert check_proposer("meera", policy) is Code.E_NOT_PROPOSER
