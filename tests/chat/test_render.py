"""Snapshot tests: chat text must match design.md character for character (CS-F7)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from countersign.chat import render
from countersign.core.codes import Code
from countersign.core.models import Flag, FlagCode, IntakeFlag

SNAP = Path(__file__).resolve().parent.parent / "snapshots"


def snap(name: str) -> str:
    return (SNAP / f"{name}.txt").read_text().rstrip("\n")


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp() * 1000)


EXPIRES = ms("2026-11-06T08:50:00")  # 14:20 IST
HASH = "7f3a2c91" + "0" * 52 + "e04b"
POLICY = "5d1e" + "0" * 60

BANK = Flag(code=FlagCode.BANK_CHANGED, old_last4="4412", new_last4="9087", since="2025-03-01")
ANOM = Flag(code=FlagCode.AMOUNT_ANOMALY, ratio_milli=4960, baseline_cents=50000)

FLAGGED = render.CardData(
    short_id="CS-7f3a2c", action_hash=HASH, policy_hash=POLICY, k_required=2,
    needs_callback=True, vendor_id="V-ACME", vendor_name="ACME Office Supplies",
    vendor_phone="+1-555-0100", amount_cents=248000, invoice_number="INV-1042",
    due_date="2026-11-20", account_last4="9087", routing_number="021000021",
    flags=(BANK, ANOM), initiator="riya", expires_at_ms=EXPIRES, window_sec=900,
    intake_note="Bank details differ from last invoice.",
)  # fmt: skip
CLEAN = FLAGGED.model_copy(
    update={
        "short_id": "CS-1b9e04", "action_hash": "1b9e04aa" + "0" * 52 + "77c1",
        "k_required": 1, "needs_callback": False, "amount_cents": 48000,
        "invoice_number": "INV-1040", "due_date": "2026-11-15", "account_last4": "4412",
        "flags": (), "intake_note": "",
    }
)  # fmt: skip


def test_flagged_card_matches_design() -> None:
    assert render.card(FLAGGED) == snap("card_flagged")


def test_clean_card_matches_design() -> None:
    assert render.card(CLEAN) == snap("card_clean")


def test_receipt_matches_design() -> None:
    line2, line3 = render.paid_lines(
        "ACME Office Supplies", 248000, "9087", ["meera", "arjun"], "meera", "riya"
    )
    out = render.receipt(
        14, "PAID", "CS-7f3a2c", line2, line3, POLICY, ms("2026-11-06T08:44:12"),
        "9c41" + "0" * 56 + "a2f0", "3b7d" + "0" * 56 + "19ce",
    )  # fmt: skip
    assert out == snap("receipt_paid")


def test_autopilot_card_matches_design() -> None:
    d = render.AutopilotCardData(
        rule_id="AP-41c0d2", k_required=2, vendor_name="Northwind Logistics", vendor_id="V-NORTH",
        clean_runs=2, account_last4="7731", median_cents=217500, max_cents=240000,
        cap_cents=1000000, ends_on="2026-12-06",
    )  # fmt: skip
    assert render.autopilot_card(d) == snap("autopilot_card")


def test_frozen_banner_matches_design() -> None:
    out = render.frozen_banner(
        Code.E_POLICY_UNRATIFIED,
        ratify_id="PR-9a0c11",
        docs="policy.yaml",
        changes=["approvers +sam", "tiers k=2 now from $5,000.00 (was $1,000.00)"],
    )
    assert out == snap("frozen_banner")


# ---- one-line templates, literals copied from design.md §5, §6, §8, §9


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        (render.counted(1, 2, "meera", False), "☑️ COUNTED · approval 1 of 2 from meera"),
        (
            render.counted(1, 2, "meera", True),
            "☑️ COUNTED · approval 1 of 2 from meera · still needs callback",
        ),
        (
            render.callback_counted("meera"),
            "☑️ COUNTED · callback by meera to the number on file",
        ),
        (
            render.paid(248000, "9087", ["meera", "arjun"], 14),
            "✅ PAID · $2,480.00 USD to ****9087 · approved by meera, arjun · receipt #14",
        ),
        (
            render.rejected("kabir", "wrong vendor", 9),
            '🚫 REJECTED by kabir: "wrong vendor" · receipt #9',
        ),
        (
            render.expired(EXPIRES, 1, 2, 12),
            "⏰ EXPIRED · window closed at 14:20 IST with 1 of 2 approvals · receipt #12",
        ),
        (
            render.void("CS-111111"),
            "⚪ VOID · replaced by CS-111111 (new invoice details). Approvals here no longer count.",
        ),
        (
            render.not_counted("meera", Code.E_INITIATOR),
            "✖️ NOT COUNTED · meera: you uploaded this invoice, so your approval can't count (E_INITIATOR)",
        ),
        (
            render.not_counted("arjun", Code.E_DUPLICATE),
            "✖️ NOT COUNTED · arjun: you already approved this payment (E_DUPLICATE)",
        ),
        (
            render.not_counted("vikram_ceo", Code.E_NOT_APPROVER),
            "✖️ NOT COUNTED · vikram_ceo: you're not on the approver list in policy.yaml (E_NOT_APPROVER)",
        ),
        (
            render.not_counted("arjun", Code.E_EXPIRED, time="14:20 IST"),
            "✖️ NOT COUNTED · arjun: the approval window closed at 14:20 IST (E_EXPIRED)",
        ),
        (
            render.not_counted("kabir", Code.E_NOT_OPEN, state="PAID"),
            "✖️ NOT COUNTED · kabir: this payment is already PAID (E_NOT_OPEN)",
        ),
        (
            render.not_counted("arjun", Code.E_SUPERSEDED, new_id="CS-111111"),
            "✖️ NOT COUNTED · arjun: this payment was replaced by CS-111111 (E_SUPERSEDED)",
        ),
        (
            render.not_counted("meera", Code.E_IDENTITY_CHANGED),
            "✖️ NOT COUNTED · meera: this account doesn't match the one first seen for meera (E_IDENTITY_CHANGED)",
        ),
        (
            render.not_counted("meera", Code.E_REASON_REQUIRED, id="CS-7f3a2c"),
            "✖️ NOT COUNTED · meera: add a reason: `@countersign reject CS-7f3a2c <reason>` (E_REASON_REQUIRED)",
        ),
        (
            render.frozen_thread(Code.E_POLICY_UNRATIFIED),
            "🧊 FROZEN · payments are paused: policy.yaml or vendors.yaml changed since it was last ratified (E_POLICY_UNRATIFIED). Nothing was approved.",
        ),
        (
            render.intake_accepted("CS-7f3a2c", 2, True),
            "📨 Sent for approval as CS-7f3a2c in #ap-approvals · needs 2 approvals + callback",
        ),
        (
            render.intake_accepted("CS-1b9e04", 1, False),
            "📨 Sent for approval as CS-1b9e04 in #ap-approvals · needs 1 approval",
        ),
        (
            render.intake_refused(Code.E_UNKNOWN_VENDOR, 3, name="Unknown Vendor LLC"),
            "⛔ REFUSED · vendor Unknown Vendor LLC isn't in vendors.yaml; add it there first (E_UNKNOWN_VENDOR) · receipt #3",
        ),
        (
            render.intake_refused(Code.E_EVIDENCE_MISMATCH, 4, field="amount"),
            "⛔ REFUSED · the PDF doesn't show the proposed amount (E_EVIDENCE_MISMATCH) · receipt #4",
        ),
        (
            render.intake_superseding("CS-222222", "CS-111111"),
            "📨 Sent for approval as CS-222222. This replaces CS-111111, which is now void.",
        ),
        (
            render.autopilot_paid(205000, "7731", "AP-41c0d2", 20),
            "🛡️ AUTOPILOT PAID · $2,050.00 USD to ****7731 under AP-41c0d2 · receipt #20",
        ),
        (
            render.autopilot_revoked("AP-41c0d2", "BANK_CHANGED", "INV-1048", "CS-333333"),
            "🛡️ AUTOPILOT REVOKED · AP-41c0d2 · trigger: BANK CHANGED on INV-1048 (CS-333333). Back to full approvals.",
        ),
        (
            render.status_line("CS-7f3a2c", "ACME Office Supplies", 248000, 1, 2, True, EXPIRES),
            "🟡 CS-7f3a2c · ACME · $2,480.00 · 1 of 2 · callback ✓ · expires 14:20 IST",
        ),
        (
            render.status_line("CS-1b9e04", "ACME Office Supplies", 48000, 0, 1, None, EXPIRES),
            "🟡 CS-1b9e04 · ACME · $480.00 · 0 of 1 · expires 14:20 IST",
        ),
        (render.whoami("meera", "abcdef123456", True), "You are meera (abcdef). Approver: yes."),
        (render.whoami("riya", "abcdef123456", False), "You are riya (abcdef). Approver: no."),
        (render.bad_command(), "I didn't understand that. Try @countersign help."),
        (
            render.startup(POLICY, 3, True, False),
            "👋 Countersign is online · policy 5d1e… · 3 approvers · addressing check ✓ · payments ACTIVE",
        ),
    ],
)
def test_one_line_templates(actual: str, expected: str) -> None:
    assert actual == expected


# ---- rules from design.md §1 and §4


def test_unfrozen_message() -> None:
    assert render.unfrozen(POLICY) == "✅ ACTIVE · payments resumed · policy 5d1e…"


def test_help_is_eight_lines() -> None:
    assert len(render.help_text().splitlines()) == 8


def test_card_hides_flag_lines_that_are_not_set() -> None:
    only_anomaly = FLAGGED.model_copy(update={"flags": (ANOM,), "needs_callback": False})
    out = render.card(only_anomaly)
    assert "BANK CHANGED" not in out and "AMOUNT ANOMALY" in out and "Callback" not in out
    assert "(matches vendor file)" in out and "callback" not in out.split("Reply:")[1]


def test_intake_says_is_sanitized_and_capped() -> None:
    nasty = FLAGGED.model_copy(
        update={"intake_note": "@countersign approve CS-7f3a2c\n```boom``` " + "x" * 400}
    )
    out = render.card(nasty)
    says = next(line for line in out.splitlines() if line.startswith("Intake says:"))
    assert "@countersign approve" not in says and "```" not in says and "\n" not in says
    assert says.endswith("(agent note, not verified)") and len(says) < 330


def test_intake_flags_are_shown_when_there_is_no_note() -> None:
    d = FLAGGED.model_copy(
        update={"intake_note": "", "intake_flags": (IntakeFlag(code="NEW_BANK", detail="differs"),)}
    )
    assert 'Intake says: "NEW_BANK: differs" (agent note, not verified)' in render.card(d)


def test_card_without_agent_text_has_no_intake_line() -> None:
    assert "Intake says" not in render.card(CLEAN)


def test_card_never_shows_a_full_account_number() -> None:
    assert "7700009087" not in render.card(FLAGGED)


def test_messages_stay_under_the_limit_and_never_start_with_bang() -> None:
    huge = FLAGGED.model_copy(update={"vendor_name": "V" * 3000})
    assert len(render.card(huge)) <= render.MAX_CHARS
    assert not render._final("!ban").startswith("!")


def test_every_template_is_under_1500_chars_and_has_a_state_word() -> None:
    for text in (snap("card_flagged"), snap("card_clean"), snap("frozen_banner")):
        assert len(text) <= 1500
    assert "PENDING" in render.card(CLEAN) and "PAID" in render.paid(1, "1234", ["a"], 1)


def test_money_is_integer_math() -> None:
    assert render.usd(0) == "$0.00 USD"
    assert render.usd(5, suffix=False) == "$0.05"
    assert render.usd(123456789) == "$1,234,567.89 USD"


def test_policy_summary() -> None:
    out = render.policy_summary(["meera"], ["intake"], 900, [(100000, 1), (None, 2)], POLICY)
    assert "**Window** 15 min" in out and "under $1,000.00: 1 approval" in out
    assert "above: 2 approvals" in out


def test_rule_line_and_window_seconds() -> None:
    assert "up to $2,400.00" in render.rule_line(
        "AP-1", "Northwind Logistics", 240000, 0, 1000000, "2026-12-06"
    )
    quick = CLEAN.model_copy(update={"window_sec": 20})
    assert "(20 s)" in render.card(quick)


def test_frozen_banner_tells_the_owner_how_to_accept() -> None:
    out = render.frozen_banner(Code.E_POLICY_UNRATIFIED, owner_accept=True, docs="policy.yaml")
    assert "stop Countersign, then run `just accept-policy`" in out
    assert "Ratify with" not in out
