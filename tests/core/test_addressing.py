from __future__ import annotations

import pytest

from countersign.core.addressing import Rule, check_addressing
from countersign.core.codes import Code

ROOM, INTAKE, AUDIT = "room-approvals", "room-intake", "room-audit"
APPROVERS = {"id-meera", "id-arjun", "id-kabir"}
ADMIN = "id-admin"


def r1(**over: object) -> Rule:
    raw: dict[str, object] = {
        "rooms": [ROOM], "users": ["id-meera", "id-arjun", "id-kabir"], "agents": [],
        "owner": False, "owner_agents": False, "platform": False,
    }  # fmt: skip
    raw.update(over)
    return Rule.model_validate(raw)


def r2() -> Rule:
    return Rule.model_validate({"rooms": [INTAKE], "users": [], "agents": ["agent-intake"]})


def r3(**over: object) -> Rule:
    raw: dict[str, object] = {"rooms": [INTAKE, ROOM, AUDIT], "users": [ADMIN], "agents": []}
    raw.update(over)
    return Rule.model_validate(raw)


def check(rules: list[Rule], admin: str | None = ADMIN) -> Code | None:
    return check_addressing(rules, ROOM, APPROVERS, admin)


def test_the_intended_rules_pass() -> None:
    assert check([r1(), r2(), r3()]) is None


def test_no_rules_is_allow_all_and_fails() -> None:
    assert check([]) is Code.E_ADDRESSING_OPEN


@pytest.mark.parametrize(
    "bad",
    [
        {"agents": "*"},  # any agent
        {"agents": ["agent-rogue"]},  # a named agent in the approvals room
        {"users": "*"},  # every human (the impersonator gets through layer 1)
        {"platform": True},
        {"owner_agents": True},
        {"users": ["id-meera", "id-vikram"]},  # an id that is not an approver
        {"users": ["id-meera", "id-admin-typo"]},
    ],
)
def test_loosened_approvals_rule_fails(bad: dict[str, object]) -> None:
    assert check([r1(**bad), r3()]) is Code.E_ADDRESSING_OPEN


def test_everything_open_preset_anyone_fails() -> None:
    # the "anyone" preset arrives as one all-"*" rule
    assert check([Rule()]) is Code.E_ADDRESSING_OPEN
    assert check([Rule(rooms="*", users="*", agents="*")]) is Code.E_ADDRESSING_OPEN


def test_owner_only_preset_is_tight_enough() -> None:
    owner_only = Rule(rooms="*", users=(), agents=(), owner=True)
    assert check([owner_only]) is None


def test_rule_for_other_rooms_may_admit_agents() -> None:
    assert check([r1(), r2(), r3(rooms=[INTAKE, AUDIT, ROOM])]) is None
    loose_elsewhere = Rule.model_validate({"rooms": [INTAKE], "users": "*", "agents": "*"})
    assert check([r1(), loose_elsewhere]) is None


def test_admin_is_allowed_only_when_known() -> None:
    assert check([r1(), r3()], admin=ADMIN) is None
    assert check([r1(), r3()], admin=None) is Code.E_ADDRESSING_OPEN


def test_wildcard_rooms_rule_covers_the_approvals_room() -> None:
    assert check([r1(), Rule(rooms="*", users=(), agents="*")]) is Code.E_ADDRESSING_OPEN


def test_switch_extra_fields_are_ignored_and_owner_flag_is_allowed() -> None:
    rule = Rule.model_validate(
        {"rooms": [ROOM], "users": [], "agents": [], "owner": True, "new": 1}
    )
    assert check([rule]) is None


def test_switch_json_shape_parses() -> None:
    live = {"rooms": ["x"], "room_groups": "*", "users": ["a"], "agents": [], "owner": False,
            "owner_agents": False, "platform": False}  # fmt: skip
    assert Rule.model_validate(live).users == ("a",)
