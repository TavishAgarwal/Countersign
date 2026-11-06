from __future__ import annotations

import pytest

from countersign.core.codes import Code
from countersign.core.evidence import match, normalize_text
from tests.conftest import make_proposal

GOOD = "ACME Office Supplies\nInvoice No: INV-1040\nTotal due: $480.00\nPay to account 7700004412"


def test_match_ok() -> None:
    res = match(make_proposal(), GOOD)
    assert res.ok and res.mismatched == () and res.code is None


@pytest.mark.parametrize(
    ("text", "amount"),
    [("Total 2,480.00 USD", 248000), ("Total $2480.00", 248000), ("Amt 480.05", 48005)],
)
def test_amount_formats(text: str, amount: int) -> None:
    body = f"INV-1040 {text} account 7700004412"
    assert match(make_proposal(amount_cents=amount), body).ok


@pytest.mark.parametrize(
    ("bad", "field"),
    [
        (GOOD.replace("INV-1040", "INV-1041"), "invoice number"),
        (GOOD.replace("INV-1040", "INV-10400"), "invoice number"),
        (GOOD.replace("$480.00", "$481.00"), "amount"),
        (GOOD.replace("$480.00", "$1,480.00"), "amount"),
        (GOOD.replace("$480.00", "$4,480.00"), "amount"),
        (GOOD.replace("7700004412", "7700009087"), "account"),
        (GOOD.replace("7700004412", "77000044123"), "account"),
    ],
)
def test_each_mismatch_is_named(bad: str, field: str) -> None:
    res = match(make_proposal(), bad)
    assert not res.ok and res.code is Code.E_EVIDENCE_MISMATCH
    assert res.mismatched == (field,)


def test_several_mismatches_are_all_named() -> None:
    res = match(make_proposal(), "nothing relevant here")
    assert res.mismatched == ("invoice number", "amount", "account")


def test_empty_text_is_unreadable() -> None:
    for blank in ("", "   \n\t "):
        res = match(make_proposal(), blank)
        assert not res.ok and res.code is Code.E_PDF_UNREADABLE


def test_account_with_separators_and_wrapping() -> None:
    assert match(make_proposal(), "INV-1040 $480.00 acct 7700-004412").ok
    assert match(make_proposal(), "INV-1040 $480.00 acct 7700 004412").ok
    assert not match(make_proposal(), "INV-1040 $480.00 acct 7700 - 004412").ok


def test_invoice_number_is_case_insensitive_but_bounded() -> None:
    assert match(make_proposal(), "inv-1040 $480.00 7700004412").ok
    assert not match(make_proposal(), "XINV-1040 $480.00 7700004412").ok


def test_normalize_text() -> None:
    assert normalize_text("a   b\n\nc") == "a b c"
