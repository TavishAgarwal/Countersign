from __future__ import annotations

import pytest

from countersign.core.policy import (
    PolicyError,
    line_diff,
    parse_documents,
    parse_policy,
    parse_vendors,
    summarize_changes,
)
from tests.conftest import ROOT

POLICY = (ROOT / "switch" / "policy.yaml").read_text()
VENDORS = (ROOT / "switch" / "vendors.yaml").read_text()


def test_real_documents_parse(docs: tuple[object, ...]) -> None:
    policy, vendors, h = docs  # type: ignore[misc]
    assert policy.approvers == ("meera", "arjun", "kabir")
    assert [v.vendor_id for v in vendors.vendors] == ["V-ACME", "V-NORTH", "V-GLOBEX", "V-QUICK"]
    assert len(h) == 64


def test_policy_helpers(docs: tuple[object, ...]) -> None:
    policy = docs[0]
    assert policy.window_ms("V-QUICK") == 20_000  # type: ignore[attr-defined]
    assert policy.window_ms("V-ACME") == 900_000  # type: ignore[attr-defined]
    assert policy.tier_k(99_999) == 1  # type: ignore[attr-defined]
    assert policy.tier_k(100_000) == 2  # type: ignore[attr-defined]


def test_hash_ignores_formatting_and_comments() -> None:
    _, _, h1 = parse_documents(POLICY, VENDORS)
    reformatted = "# another comment\n" + POLICY.replace(
        "[meera, arjun, kabir]", "\n  - meera\n  - arjun\n  - kabir"
    )
    _, _, h2 = parse_documents(reformatted, VENDORS)
    assert h1 == h2


def test_hash_changes_on_policy_edit() -> None:
    _, _, h1 = parse_documents(POLICY, VENDORS)
    _, _, h2 = parse_documents(POLICY.replace("window_sec: 900", "window_sec: 600"), VENDORS)
    assert h1 != h2


def test_hash_changes_on_vendor_edit() -> None:
    _, _, h1 = parse_documents(POLICY, VENDORS)
    _, _, h2 = parse_documents(POLICY, VENDORS.replace("7700004412", "7700009087"))
    assert h1 != h2


@pytest.mark.parametrize(
    "bad",
    [
        "approvers: [a\n",  # invalid YAML
        "- just\n- a list\n",
        POLICY + "\nsurprise_key: 1\n",
        POLICY.replace("approvers: [meera, arjun, kabir]", "approvers: []"),
        POLICY.replace("approvers: [meera, arjun, kabir]", "approvers: [meera, meera]"),
        POLICY.replace("window_sec: 900", "window_sec: 0"),
        POLICY.replace("window_sec: 900", "window_sec: 9.5"),
        POLICY.replace("{below_cents: null, k: 2}", "{below_cents: 5, k: 2}"),
        POLICY.replace("{below_cents: null, k: 2}", "{below_cents: null, k: 9}"),
        POLICY.replace("amount_anomaly_factor: 2.0", "amount_anomaly_factor: 0"),
        POLICY.replace("amount_anomaly_factor: 2.0", "amount_anomaly_factor: 2.0001"),
        POLICY.replace("callback_required: true", "callback_required: maybe"),
    ],
)
def test_malformed_policy_is_an_error(bad: str) -> None:
    with pytest.raises(PolicyError):
        parse_policy(bad)


@pytest.mark.parametrize(
    "bad",
    [
        "vendors: not-a-list",
        VENDORS.replace('"7700004412"', '"77x"'),
        VENDORS.replace("baseline_cents: 50000", "baseline_cents: 0"),
        VENDORS.replace("bank_on_file_since: 2025-03-01", "bank_on_file_since: march"),
        VENDORS + "  - vendor_id: V-ACME\n    name: Dup\n",
    ],
)
def test_malformed_vendors_is_an_error(bad: str) -> None:
    with pytest.raises(PolicyError):
        parse_vendors(bad)


def test_line_diff_shows_added_and_removed_lines() -> None:
    diff = line_diff("a: 1\nb: 2\n", "a: 1\nb: 3\nc: 4\n")
    assert diff == ["- b: 2", "+ b: 3", "+ c: 4"]


def test_summarize_changes() -> None:
    old = parse_policy(POLICY)
    new = parse_policy(
        POLICY.replace("[meera, arjun, kabir]", "[meera, arjun, kabir, sam]").replace(
            "window_sec: 900", "window_sec: 600"
        )
    )
    assert summarize_changes(old, new) == ["approvers +sam", "window_sec changed"]
    assert summarize_changes(old, old) == []
