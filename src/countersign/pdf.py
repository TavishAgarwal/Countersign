"""Extract text from an invoice PDF with pypdf (I/O wrapper; text PDFs only, decision D6)."""

from __future__ import annotations

import io

from pypdf import PdfReader

MAX_PAGES = 20
MAX_CHARS = 200_000


class PdfError(RuntimeError):
    """The bytes are not a readable text PDF (the handler turns this into ``E_PDF_UNREADABLE``)."""


def extract_text(data: bytes) -> str:
    """Return the text of up to 20 pages (capped at 200,000 characters). Raises PdfError."""
    if not data.startswith(b"%PDF"):
        raise PdfError("not a PDF")
    try:
        reader = PdfReader(io.BytesIO(data))
        parts = [(page.extract_text() or "") for page in reader.pages[:MAX_PAGES]]
    except Exception as exc:  # pypdf raises many types on malformed files
        raise PdfError(type(exc).__name__) from exc
    text = "\n".join(parts)
    return text[:MAX_CHARS]
