from __future__ import annotations

import ast
from pathlib import Path

from countersign.core import codes
from countersign.core.codes import Code, plain_reason


def test_every_code_has_a_docstring() -> None:
    tree = ast.parse(Path(codes.__file__).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Code")
    body = cls.body
    for i, node in enumerate(body):
        if isinstance(node, ast.Assign):
            nxt = body[i + 1]
            assert isinstance(nxt, ast.Expr) and isinstance(nxt.value, ast.Constant), node.lineno


def test_every_code_has_plain_text_and_is_named_like_itself() -> None:
    for code in Code:
        assert code.value == code.name
        assert plain_reason(code)


def test_all_architecture_codes_exist() -> None:
    expected = {
        "E_BAD_PROPOSAL", "E_NOT_PROPOSER", "E_NO_SOURCE", "E_UNKNOWN_VENDOR",
        "E_EVIDENCE_MISMATCH", "E_PDF_UNREADABLE", "E_WRONG_ROOM", "E_NOT_APPROVER",
        "E_NOT_HUMAN", "E_IDENTITY_CHANGED", "E_UNKNOWN_ID", "E_NOT_OPEN", "E_SUPERSEDED",
        "E_EXPIRED", "E_INITIATOR", "E_DUPLICATE", "E_CALLBACK_REQUIRED", "E_REASON_REQUIRED",
        "E_FROZEN", "E_POLICY_UNRATIFIED", "E_ADDRESSING_OPEN", "E_SELF_CHECK",
        "E_BAD_COMMAND", "E_INTERNAL",
    }  # fmt: skip
    assert {c.value for c in Code} == expected


def test_plain_reason_fills_placeholders() -> None:
    assert plain_reason(Code.E_SUPERSEDED, new_id="CS-111111") == (
        "this payment was replaced by CS-111111"
    )
    assert "{" not in plain_reason(Code.E_EXPIRED, time="14:20 IST")
