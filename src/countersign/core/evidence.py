"""Match a proposal against the text extracted from its PDF (CS-F5, decision D6)."""

from __future__ import annotations

import re

from countersign.core.codes import Code
from countersign.core.models import Frozen, Proposal


class EvidenceResult(Frozen):
    """Match result. ``mismatched`` names the fields not found in the PDF text."""

    ok: bool
    mismatched: tuple[str, ...]
    code: Code | None


def normalize_text(text: str) -> str:
    """Collapse all whitespace runs (including non-breaking spaces) to single spaces."""
    return re.sub(r"\s+", " ", text.replace(chr(0xA0), " ")).strip()


def _amount_forms(amount_cents: int) -> tuple[str, ...]:
    dollars, cents = divmod(amount_cents, 100)
    return (f"{dollars:,}.{cents:02d}", f"{dollars}.{cents:02d}")


def _has_amount(text: str, amount_cents: int) -> bool:
    for form in _amount_forms(amount_cents):
        if re.search(r"(?<![\d.,])" + re.escape(form) + r"(?!\d)", text):
            return True
    return False


def _has_invoice_number(text: str, number: str) -> bool:
    pattern = r"(?<![A-Za-z0-9])" + re.escape(number) + r"(?![A-Za-z0-9])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _has_account(text: str, account: str) -> bool:
    # Allow a single space or dash between digits ("7700-009087"), but never extra digits.
    spaced = r"[ -]?".join(re.escape(d) for d in account)
    return re.search(r"(?<!\d)" + spaced + r"(?!\d)", text) is not None


def match(proposal: Proposal, pdf_text: str) -> EvidenceResult:
    """Check that invoice number, amount and account number appear in the PDF text.

    Amount formats ``2,480.00`` and ``2480.00`` are accepted. Reason codes:
    ``E_PDF_UNREADABLE`` when the text is empty, ``E_EVIDENCE_MISMATCH`` when a field is absent.
    """
    text = normalize_text(pdf_text)
    if not text:
        return EvidenceResult(ok=False, mismatched=(), code=Code.E_PDF_UNREADABLE)
    missing: list[str] = []
    if not _has_invoice_number(text, proposal.invoice_number):
        missing.append("invoice number")
    if not _has_amount(text, proposal.amount_cents):
        missing.append("amount")
    if not _has_account(text, proposal.bank.account_number):
        missing.append("account")
    if missing:
        return EvidenceResult(ok=False, mismatched=tuple(missing), code=Code.E_EVIDENCE_MISMATCH)
    return EvidenceResult(ok=True, mismatched=(), code=None)
