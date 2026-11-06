"""Build fixtures/invoices/<id>.pdf from fixtures/invoices.yaml (architecture.md §8.2).

Plain text PDFs with core fonts, so pypdf extracts exactly what is printed. Deterministic: the
creation date is fixed. INV-1042 carries a hidden white-text line (a labelled injection fixture).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from fpdf import FPDF

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "fixtures" / "invoices.yaml"
OUT = ROOT / "fixtures" / "invoices"


def usd(cents: int) -> str:
    return f"{cents // 100:,}.{cents % 100:02d}"


def build(inv: dict[str, object]) -> bytes:
    pdf = FPDF()
    pdf.set_creation_date(datetime(2026, 11, 1, tzinfo=UTC))
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, str(inv["vendor_name"]), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 12)
    lines = [
        f"Invoice {inv['number']}",
        f"Invoice date: {inv['invoice_date']}",
        f"Due date: {inv['due_date']}",
        "Bill to: Example Corp Accounts Payable",
        "",
        f"Amount due: ${usd(int(str(inv['amount_cents'])))} USD",
        "",
        "Payment instructions",
        f"Account number: {inv['account_number']}",
        f"Routing number: {inv['routing_number']}",
    ]
    if inv.get("note"):
        lines.insert(1, str(inv["note"]))
    for line in lines:
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    if inv.get("hidden_text"):
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "", 6)
        pdf.cell(0, 4, str(inv["hidden_text"]), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def main() -> int:
    data = yaml.safe_load(SRC.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    for inv in data["invoices"]:
        (OUT / f"{inv['id']}.pdf").write_bytes(build(inv))
    print(f"wrote {len(data['invoices'])} PDFs to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
