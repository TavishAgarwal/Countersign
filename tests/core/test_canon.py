from __future__ import annotations

import pytest

from countersign.core.canon import action_hash, canonical_bytes, short_id
from tests.conftest import make_proposal


def test_key_order_does_not_change_bytes() -> None:
    assert canonical_bytes({"b": 1, "a": {"y": 2, "x": 3}}) == canonical_bytes(
        {"a": {"x": 3, "y": 2}, "b": 1}
    )


def test_exact_format_is_compact_sorted_utf8() -> None:
    assert canonical_bytes({"b": "é", "a": [1, 2]}) == '{"a":[1,2],"b":"é"}'.encode()


def test_hash_is_stable_lowercase_hex(phash: str) -> None:
    _, h1 = action_hash(make_proposal(), phash)
    _, h2 = action_hash(make_proposal(), phash)
    assert h1 == h2 and len(h1) == 64 and h1 == h1.lower()


@pytest.mark.parametrize(
    "over",
    [
        {"amount_cents": 48001},
        {"vendor_id": "V-NORTH"},
        {"invoice_number": "INV-1041"},
        {"bank": {"account_number": "7700009087", "routing_number": "021000021"}},
        {"bank": {"account_number": "7700004412", "routing_number": "026009593"}},
        {"due_date": "2026-11-16"},
        {"source_message_id": "msg-2"},
    ],
)
def test_any_field_change_changes_the_hash(phash: str, over: dict[str, object]) -> None:
    assert action_hash(make_proposal(), phash)[1] != action_hash(make_proposal(**over), phash)[1]


def test_policy_hash_is_part_of_the_hash(phash: str) -> None:
    p = make_proposal()
    assert action_hash(p, phash)[1] != action_hash(p, "0" * 64)[1]


def test_advisory_fields_do_not_change_the_hash(phash: str) -> None:
    base = action_hash(make_proposal(), phash)[1]
    noisy = make_proposal(
        intake_note="looks fine", intake_flags=[{"code": "X", "detail": "advisory"}]
    )
    assert action_hash(noisy, phash)[1] == base


def test_account_formatting_is_normalized(phash: str) -> None:
    spaced = make_proposal(bank={"account_number": "7700 004-412", "routing_number": "021000021"})
    assert action_hash(spaced, phash)[1] == action_hash(make_proposal(), phash)[1]


def test_short_id_format_and_prefix() -> None:
    h = "7f3a2c91" + "0" * 56
    assert short_id(h) == "CS-7f3a2c"
    assert short_id(h, prefix="AP") == "AP-7f3a2c"


def test_short_id_extends_to_8_on_collision() -> None:
    h = "7f3a2c91" + "0" * 56
    assert short_id(h, taken={"CS-7f3a2c"}) == "CS-7f3a2c91"
    assert short_id(h, taken={"CS-7f3a2c55"}) == "CS-7f3a2c91"
    assert short_id(h, taken={"CS-111111"}) == "CS-7f3a2c"


def test_short_id_fails_if_8_also_collide() -> None:
    h = "7f3a2c91" + "0" * 56
    with pytest.raises(ValueError):
        short_id(h, taken={"CS-7f3a2c", "CS-7f3a2c91"})
