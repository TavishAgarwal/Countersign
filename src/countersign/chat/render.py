"""Every chat message Countersign posts (design.md §2-§9). The only place chat text lives.

Plain functions from decision data to strings: no I/O, no clock. Formatting rules: bold,
line breaks, inline code and bullets only; at most 1,500 characters; never start with ``!``;
account numbers shown as last four digits only. Agent-supplied text is sanitized so it cannot
mention, command or format anything.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone

from countersign.core.codes import Code, plain_reason
from countersign.core.models import Flag, FlagCode, Frozen, IntakeFlag

MAX_CHARS = 1500
IST = timezone(timedelta(hours=5, minutes=30), "IST")
_ZWSP = "​"
HELP_LINES = (
    "`@countersign approve <id>` · approve a payment",
    "`@countersign reject <id> <reason>` · reject with a reason",
    "`@countersign callback <id>` · confirm a bank change by phone",
    "`@countersign status` · open payments and autopilot rules",
    "`@countersign status <id>` · one payment in full",
    "`@countersign whoami` · how Countersign sees you",
    "`@countersign policy` · policy summary and hash",
    "`@countersign autopilot list` · or `autopilot revoke <vendor_id>`",
)


# ---------------------------------------------------------------- helpers


def usd(cents: int, suffix: bool = True) -> str:
    """``$2,480.00 USD`` (or ``$2,480.00`` when ``suffix`` is False). Integer math only."""
    dollars, rem = divmod(cents, 100)
    text = f"${dollars:,}.{rem:02d}"
    return f"{text} USD" if suffix else text


def ist_time(ts_ms: int) -> str:
    """Server time shown as ``14:05 IST`` (display only)."""
    return datetime.fromtimestamp(ts_ms / 1000, IST).strftime("%H:%M") + " IST"


def iso_utc(ts_ms: int) -> str:
    """ISO 8601 UTC, second precision, for receipts."""
    return datetime.fromtimestamp(ts_ms // 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def short_hash(digest: str) -> str:
    """First 4 and last 4 hex characters: ``9c41…a2f0``."""
    return f"{digest[:4]}…{digest[-4:]}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _needs(k: int, callback: bool) -> str:
    return f"needs {_plural(k, 'approval')}" + (" + callback" if callback else "")


def _window(sec: int) -> str:
    return f"{sec // 60} min" if sec % 60 == 0 else f"{sec} s"


def sanitize(text: str, limit: int) -> str:
    """Make agent text inert: one line, no backticks, mentions broken, length-capped."""
    flat = re.sub(r"\s+", " ", text).replace("`", "'").replace("@", "@" + _ZWSP).strip()
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _final(text: str) -> str:
    out = text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 1] + "…"
    return "· " + out if out.startswith("!") else out


def flag_line(flag: Flag) -> str:
    """The card line for a flag (design.md §2)."""
    if flag.code is FlagCode.BANK_CHANGED:
        return (
            f"⚠️ BANK CHANGED: ****{flag.old_last4} → ****{flag.new_last4} "
            f"(on file since {flag.since})"
        )
    if flag.code is FlagCode.AMOUNT_ANOMALY:
        tenths = ((flag.ratio_milli or 0) + 50) // 100
        return (
            f"⚠️ AMOUNT ANOMALY: {tenths // 10}.{tenths % 10}× the usual "
            f"{usd(flag.baseline_cents or 0, suffix=False)}"
        )
    return f"⚠️ This invoice revokes autopilot {flag.rule_id}"


# ---------------------------------------------------------------- the card (§4)


class CardData(Frozen):
    """Everything on an approval card. Built by Countersign from its own records."""

    short_id: str
    action_hash: str
    policy_hash: str
    k_required: int
    needs_callback: bool
    vendor_id: str
    vendor_name: str
    vendor_phone: str
    amount_cents: int
    invoice_number: str
    due_date: str
    account_last4: str
    routing_number: str
    flags: tuple[Flag, ...]
    initiator: str
    expires_at_ms: int
    window_sec: int
    intake_note: str = ""
    intake_flags: tuple[IntakeFlag, ...] = ()


def _intake_says(note: str, flags: Sequence[IntakeFlag]) -> str | None:
    text = note or "; ".join(f"{f.code}: {f.detail}" for f in flags)
    if not text:
        return None
    return f'Intake says: "{sanitize(text, 280)}" (agent note, not verified)'


def card(d: CardData) -> str:
    """The trusted approval card for ``#ap-approvals``."""
    bank_changed = any(f.code is FlagCode.BANK_CHANGED for f in d.flags)
    lines = [
        f"🟡 PENDING · Payment {d.short_id} · {_needs(d.k_required, d.needs_callback)}",
        "",
        f"**Pay** {d.vendor_name} ({d.vendor_id})",
        f"**Amount** {usd(d.amount_cents)} · invoice {d.invoice_number} · due {d.due_date}",
        f"**To account** ****{d.account_last4} · routing {d.routing_number}"
        + ("" if bank_changed else " (matches vendor file)"),
        *(flag_line(f) for f in d.flags),
        "**Evidence** PDF matches: invoice no. ✓ · amount ✓ · account ✓",
        f"**Uploaded by** {d.initiator} (can't approve)",
    ]
    if d.needs_callback:
        lines.append(
            f"**Callback** required: call {d.vendor_phone} "
            "(number on file, not the one on the invoice)"
        )
    lines.append(f"**Expires** {ist_time(d.expires_at_ms)} ({_window(d.window_sec)})")
    lines.append("")
    says = _intake_says(d.intake_note, d.intake_flags)
    if says:
        lines += [says, ""]
    reply = [
        f"`@countersign approve {d.short_id}`",
        f"`@countersign reject {d.short_id} <reason>`",
    ]
    if d.needs_callback:
        reply.append(f"`@countersign callback {d.short_id}`")
    lines.append("Reply: " + " · ".join(reply))
    lines.append(f"hash {d.action_hash[:8]}…{d.action_hash[-4:]} · policy {d.policy_hash[:4]}…")
    return _final("\n".join(lines))


# ---------------------------------------------------------------- card thread (§5)


def counted(n: int, k: int, username: str, callback_missing: bool) -> str:
    """An approval was counted."""
    tail = " · still needs callback" if callback_missing else ""
    return f"☑️ COUNTED · approval {n} of {k} from {username}{tail}"


def callback_counted(username: str) -> str:
    """A callback was recorded."""
    return f"☑️ COUNTED · callback by {username} to the number on file"


def paid(amount_cents: int, last4: str, approvers: Sequence[str], seq: int) -> str:
    """The payment executed."""
    who = ", ".join(approvers)
    return f"✅ PAID · {usd(amount_cents)} to ****{last4} · approved by {who} · receipt #{seq}"


def rejected(username: str, reason: str, seq: int) -> str:
    """An approver rejected the payment."""
    return f'🚫 REJECTED by {username}: "{sanitize(reason, 200)}" · receipt #{seq}'


def expired(closed_at_ms: int, n: int, k: int, seq: int) -> str:
    """The window closed before quorum."""
    return (
        f"⏰ EXPIRED · window closed at {ist_time(closed_at_ms)} with {n} of {k} approvals "
        f"· receipt #{seq}"
    )


def void(new_id: str) -> str:
    """The proposal was replaced by a newer one."""
    return f"⚪ VOID · replaced by {new_id} (new invoice details). Approvals here no longer count."


def not_counted(username: str, code: Code, **fields: str) -> str:
    """An attempt did not count; says why."""
    return f"✖️ NOT COUNTED · {username}: {plain_reason(code, username=username, **fields)} ({code})"


def frozen_thread(code: Code, **fields: str) -> str:
    """Reply to an action that hits a frozen state."""
    return (
        f"🧊 FROZEN · payments are paused: {plain_reason(code, **fields)} ({code}). "
        "Nothing was approved."
    )


# ---------------------------------------------------------------- intake thread (§6)


def intake_accepted(short_id: str, k: int, callback: bool) -> str:
    """A proposal became a card."""
    return f"📨 Sent for approval as {short_id} in #ap-approvals · {_needs(k, callback)}"


def intake_refused(code: Code, seq: int, **fields: str) -> str:
    """A proposal was refused by a check."""
    return f"⛔ REFUSED · {plain_reason(code, **fields)} ({code}) · receipt #{seq}"


def intake_superseding(new_id: str, old_id: str) -> str:
    """A proposal replaced an open one."""
    return f"📨 Sent for approval as {new_id}. This replaces {old_id}, which is now void."


def autopilot_paid(amount_cents: int, last4: str, rule_id: str, seq: int) -> str:
    """An invoice was paid under an autopilot rule."""
    return f"🛡️ AUTOPILOT PAID · {usd(amount_cents)} to ****{last4} under {rule_id} · receipt #{seq}"


# ---------------------------------------------------------------- receipts (§7)


def receipt(
    seq: int,
    rtype: str,
    ref: str,
    line2: str,
    line3: str,
    policy_hash: str,
    ts_ms: int,
    digest: str,
    prev_digest: str,
) -> str:
    """The five-line receipt posted to ``#ap-audit``."""
    return _final(
        "\n".join(
            [
                f"🧾 RECEIPT #{seq} · {rtype} · {ref}",
                line2,
                line3,
                f"Policy {policy_hash[:4]}… · {iso_utc(ts_ms)}",
                f"digest {short_hash(digest)} · prev {short_hash(prev_digest)}",
            ]
        )
    )


def paid_lines(
    vendor_name: str,
    amount_cents: int,
    last4: str,
    approvers: Sequence[str],
    callback_by: str | None,
    initiator: str,
) -> tuple[str, str]:
    """Lines 2 and 3 of a PAID receipt."""
    cb = f" · callback {callback_by}" if callback_by else ""
    return (
        f"{vendor_name} · {usd(amount_cents)} → ****{last4}",
        f"Approved by {', '.join(approvers)}{cb} · initiator {initiator}",
    )


# ---------------------------------------------------------------- autopilot and policy (§8)


class AutopilotCardData(Frozen):
    """Everything on an autopilot proposal card."""

    rule_id: str
    k_required: int
    vendor_name: str
    vendor_id: str
    clean_runs: int
    account_last4: str
    median_cents: int
    max_cents: int
    cap_cents: int
    ends_on: str


def autopilot_card(d: AutopilotCardData) -> str:
    """Autopilot proposal card."""
    return _final(
        "\n".join(
            [
                f"🛡️ AUTOPILOT PROPOSED · {d.rule_id} · {_needs(d.k_required, False)}",
                "",
                f"{d.vendor_name} ({d.vendor_id}) has {d.clean_runs} clean payments to "
                f"****{d.account_last4} (median {usd(d.median_cents, suffix=False)}).",
                f"**Rule** pay invoices up to {usd(d.max_cents, suffix=False)} to "
                f"****{d.account_last4} without approvals",
                f"**Limits** {usd(d.cap_cents, suffix=False)} total · ends {d.ends_on} · "
                "stops at the first warning flag",
                f"Reply: `@countersign approve {d.rule_id}` · "
                f"`@countersign reject {d.rule_id} <reason>`",
            ]
        )
    )


def autopilot_revoked(rule_id: str, trigger: str, invoice: str, short_id: str) -> str:
    """Thread reply on the rule card when it is revoked."""
    return (
        f"🛡️ AUTOPILOT REVOKED · {rule_id} · trigger: {trigger.replace('_', ' ')} on "
        f"{invoice} ({short_id}). Back to full approvals."
    )


def autopilot_revoked_by(rule_id: str, username: str) -> str:
    """Thread reply when an approver ends a rule with ``autopilot revoke``."""
    return (
        f"🛡️ AUTOPILOT REVOKED · {rule_id} · trigger: revoked by {username}. Back to full approvals."
    )


def frozen_banner(
    code: Code,
    ratify_id: str | None = None,
    ratify_k: int = 2,
    changes: Sequence[str] = (),
    owner_accept: bool = False,
    **fields: str,
) -> str:
    """The 🧊 FROZEN banner. Policy freezes add the ratification line and the change summary."""
    lines = ["🧊 FROZEN · payments are paused", f"Reason: {plain_reason(code, **fields)} ({code})."]
    if ratify_id:
        lines.append(
            f"Ratify with `@countersign approve {ratify_id}` "
            f"(needs {ratify_k} approvers of the previous policy)."
        )
    if owner_accept:
        lines.append(
            "The owner can accept this change: stop Countersign, then run `just accept-policy`."
        )
    if changes:
        lines.append("Changes: " + " · ".join(changes))
    return _final("\n".join(lines))


def unfrozen(policy_hash: str) -> str:
    """Payments resumed after a freeze."""
    return f"✅ ACTIVE · payments resumed · policy {policy_hash[:4]}…"


def startup(policy_hash: str, n_approvers: int, addressing_ok: bool, frozen: bool) -> str:
    """Posted once per start in ``#ap-approvals``."""
    check = "✓" if addressing_ok else "✗"
    state = "🧊 FROZEN" if frozen else "ACTIVE"
    return (
        f"👋 Countersign is online · policy {policy_hash[:4]}… · {n_approvers} approvers "
        f"· addressing check {check} · payments {state}"
    )


# ---------------------------------------------------------------- commands (§9)


def status_line(
    short_id: str,
    vendor_name: str,
    amount_cents: int,
    n: int,
    k: int,
    callback: bool | None,
    expires_at_ms: int,
) -> str:
    """One OPEN proposal. ``callback`` is None when not required, else whether it is done."""
    cb = "" if callback is None else f" · callback {'✓' if callback else '✗'}"
    return (
        f"🟡 {short_id} · {vendor_name.split()[0]} · {usd(amount_cents, suffix=False)} "
        f"· {n} of {k}{cb} · expires {ist_time(expires_at_ms)}"
    )


def rule_line(
    rule_id: str, vendor_name: str, max_cents: int, spent_cents: int, cap_cents: int, ends_on: str
) -> str:
    """One ACTIVE autopilot rule."""
    return (
        f"🛡️ AUTOPILOT ACTIVE · {rule_id} · {vendor_name.split()[0]} · up to "
        f"{usd(max_cents, suffix=False)} · {usd(spent_cents, suffix=False)} of "
        f"{usd(cap_cents, suffix=False)} used · ends {ends_on}"
    )


def whoami(username: str, sender_id: str, is_approver: bool) -> str:
    """Answer to ``whoami``."""
    return f"You are {username} ({sender_id[:6]}). Approver: {'yes' if is_approver else 'no'}."


def help_text() -> str:
    """Answer to ``help`` (8 lines)."""
    return "\n".join(HELP_LINES)


def bad_command() -> str:
    """Answer to an unknown command."""
    return plain_reason(Code.E_BAD_COMMAND)


def policy_summary(
    approvers: Sequence[str],
    proposers: Sequence[str],
    window_sec: int,
    tiers: Sequence[tuple[int | None, int]],
    policy_hash: str,
) -> str:
    """One-screen policy summary."""
    tier_text = " · ".join(
        f"under {usd(b, suffix=False)}: {_plural(k, 'approval')}"
        if b is not None
        else f"above: {_plural(k, 'approval')}"
        for b, k in tiers
    )
    return _final(
        "\n".join(
            [
                f"**Approvers** {', '.join(approvers)}",
                f"**Proposers** {', '.join(proposers)}",
                f"**Window** {_window(window_sec)}",
                f"**Tiers** {tier_text}",
                f"**Policy hash** {policy_hash[:8]}…{policy_hash[-4:]}",
            ]
        )
    )
