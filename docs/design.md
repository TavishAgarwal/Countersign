# Countersign — Design (UI details)

Countersign has **no web UI**. Its interface is (1) chat messages in three Mattermost channels, (2) four CLI outputs, and (3) the README and demo video. This document fixes the exact wording, layout and behavior of each. Implementation: `src/countersign/chat/render.py`; snapshot tests in `tests/snapshots/` must match these templates character for character, except for values in `{braces}`.

Related: [PRD.md](PRD.md) · [architecture.md](architecture.md) · [rules.md](rules.md) · [phases.md](phases.md)

---

## 1. Design principles

1. **The card is the truth.** Everything an approver needs is on the card, built by Countersign, not by an agent.
2. **State is always in words.** Each status has an emoji *and* a capitalized word (`✅ PAID`), so it's readable without emoji and by screen readers.
3. **One short id per thing.** `CS-xxxxxx` payments, `AP-xxxxxx` autopilot rules, `PR-xxxxxx` policy ratifications. Ids appear in every related message.
4. **Replies go in the card's thread.** The channel shows one card per proposal; the thread holds the story.
5. **Refusals are calm and specific.** Say what was refused, the code, and what would work instead. No blame, no exclamation marks.
6. **Portable formatting.** Bold, line breaks, inline code and bullets only. No Markdown tables, headings, images or HTML in chat (Slack doesn't render tables). Max 1,500 characters.
7. **Never start a message with `!`** (Switch runs `!` commands) and never print full account numbers (last 4 only).

## 2. Visual vocabulary

| State | Emoji + word | Used in |
|---|---|---|
| Waiting for approvals | `🟡 PENDING` | cards, status |
| Paid | `✅ PAID` | thread, audit |
| Refused by a check | `⛔ REFUSED` | intake thread, card thread |
| Rejected by an approver | `🚫 REJECTED` | card thread |
| Expired | `⏰ EXPIRED` | card thread |
| Replaced by a newer proposal | `⚪ VOID` | old card thread |
| Approval counted | `☑️ COUNTED` | card thread |
| Attempt not counted | `✖️ NOT COUNTED` | card thread |
| Warning flag on a card | `⚠️` + flag name | cards |
| Autopilot | `🛡️ AUTOPILOT` (`PROPOSED`/`ACTIVE`/`PAID`/`REVOKED`) | approvals, audit |
| Payments frozen | `🧊 FROZEN` | banner, status |
| Receipt | `🧾 RECEIPT` | audit |

Amounts: `$2,480.00 USD`. Accounts: `****9087`. Times: `14:05 IST` in chat (server time converted to Asia/Kolkata for display only); ISO 8601 UTC in receipts and CLI.

Flags shown on cards:

| Flag | Card text |
|---|---|
| `BANK_CHANGED` | `⚠️ BANK CHANGED: ****4412 → ****9087 (on file since {date})` |
| `AMOUNT_ANOMALY` | `⚠️ AMOUNT ANOMALY: {x.x}× the usual {baseline}` |
| `AUTOPILOT_REVOKES` | `⚠️ This invoice revokes autopilot {AP-id}` |

## 3. Channels and who sees what

| Channel | Purpose | Members | Who can address @countersign here (layer 1) |
|---|---|---|---|
| `#ap-intake` | Upload invoices; intake agent works; proposal receipts | riya, meera, arjun, kabir, admin, `intake`, `scripted-intake`, `countersign` | `intake`, `scripted-intake`, Switch admin (ops only) |
| `#ap-approvals` | Cards and approvals | meera, arjun, kabir, riya (read), vikram_ceo, admin, `countersign`, `rogue` (red team) | meera, arjun, kabir, Switch admin (ops only; the admin is not an approver) |
| `#ap-audit` | Receipts only (read-mostly) | everyone above, `countersign` | Switch admin (ops only) |

Channel headers (set by the template's room instructions/description):
- `#ap-intake`: "Drop invoices here and mention @intake. The intake agent can't pay; Countersign decides."
- `#ap-approvals`: "Approve with `@countersign approve CS-xxxxxx`. Only listed approvers count. Agents can't approve."
- `#ap-audit`: "Hash-chained receipts from Countersign. Verify with `just verify`."

Display names on Mattermost: `Riya Shah (AP Clerk)`, `Meera Iyer (Controller)`, `Arjun Mehta (CFO)`, `Kabir Khan (Finance Manager)`, `Vikram Rao (CEO)` for the impersonator (looks real on purpose). Agents: `Intake`, `Countersign`, `Scripted Intake`, `Rogue (red team)`.

## 4. The approval card (`#ap-approvals`, top-level)

```
🟡 PENDING · Payment CS-7f3a2c · needs 2 approvals + callback

**Pay** ACME Office Supplies (V-ACME)
**Amount** $2,480.00 USD · invoice INV-1042 · due 2026-11-20
**To account** ****9087 · routing 021000021
⚠️ BANK CHANGED: ****4412 → ****9087 (on file since 2025-03-01)
⚠️ AMOUNT ANOMALY: 5.0× the usual $500.00
**Evidence** PDF matches: invoice no. ✓ · amount ✓ · account ✓
**Uploaded by** riya (can't approve)
**Callback** required: call +1-555-0100 (number on file, not the one on the invoice)
**Expires** 14:20 IST (15 min)

Intake says: "Bank details differ from last invoice." (agent note, not verified)

Reply: `@countersign approve CS-7f3a2c` · `@countersign reject CS-7f3a2c <reason>` · `@countersign callback CS-7f3a2c`
hash 7f3a2c91…e04b · policy 5d1e…
```

Rules:
- Lines with a warning flag appear only when that flag is set, in the order of the flag table.
- The "Intake says" line appears only when the agent sent a note or flags, is quoted, at most 280 characters, and always ends with "(agent note, not verified)".
- "needs N approvals" pluralizes correctly; "+ callback" only when required.
- Hash shows the first 8 and last 4 hex characters.

Clean card (no flags, k=1):

```
🟡 PENDING · Payment CS-1b9e04 · needs 1 approval

**Pay** ACME Office Supplies (V-ACME)
**Amount** $480.00 USD · invoice INV-1040 · due 2026-11-15
**To account** ****4412 · routing 021000021 (matches vendor file)
**Evidence** PDF matches: invoice no. ✓ · amount ✓ · account ✓
**Uploaded by** riya (can't approve)
**Expires** 14:20 IST (15 min)

Reply: `@countersign approve CS-1b9e04` · `@countersign reject CS-1b9e04 <reason>`
hash 1b9e04aa…77c1 · policy 5d1e…
```

## 5. Thread replies (on the card)

| Event | Text |
|---|---|
| Approval counted | `☑️ COUNTED · approval {n} of {k} from {username}` (+ ` · still needs callback` if missing) |
| Callback recorded | `☑️ COUNTED · callback by {username} to the number on file` |
| Paid | `✅ PAID · {amount} to ****{last4} · approved by {u1}, {u2} · receipt #{seq}` |
| Rejected | `🚫 REJECTED by {username}: "{reason}" · receipt #{seq}` |
| Expired | `⏰ EXPIRED · window closed at {time} with {n} of {k} approvals · receipt #{seq}` |
| Void | `⚪ VOID · replaced by {new short id} (new invoice details). Approvals here no longer count.` |
| Not counted (layer 2) | `✖️ NOT COUNTED · {username}: {plain reason} ({CODE})` |
| Frozen | `🧊 FROZEN · payments are paused: {reason} ({CODE}). Nothing was approved.` |

Plain reasons for "not counted":

| Code | Plain reason |
|---|---|
| `E_INITIATOR` | you uploaded this invoice, so your approval can't count |
| `E_DUPLICATE` | you already approved this payment |
| `E_NOT_APPROVER` | you're not on the approver list in policy.yaml |
| `E_NOT_HUMAN` | this name belongs to an agent; agents can't approve |
| `E_IDENTITY_CHANGED` | this account doesn't match the one first seen for {username} |
| `E_EXPIRED` | the approval window closed at {time} |
| `E_NOT_OPEN` | this payment is already {state} |
| `E_SUPERSEDED` | this payment was replaced by {new id} |
| `E_CALLBACK_REQUIRED` | approvals are complete, but the bank change still needs a callback |
| `E_UNKNOWN_ID` | there's no payment {id} |
| `E_REASON_REQUIRED` | add a reason: `@countersign reject {id} <reason>` |
| `E_WRONG_ROOM` | approvals only count in #ap-approvals |

System reasons (banner, `status`, and the thread reply when an action hits a frozen state):

| Code | Plain reason |
|---|---|
| `E_FROZEN` | payments are paused; see the 🧊 FROZEN banner in #ap-approvals |
| `E_POLICY_UNRATIFIED` | policy.yaml or vendors.yaml changed since it was last ratified |
| `E_ADDRESSING_OPEN` | Countersign's Switch addressing rules are looser than required (agents or unlisted people could reach it in #ap-approvals) |
| `E_SELF_CHECK` | a startup check failed: {which check} |
| `E_INTERNAL` | something went wrong on my side; nothing was approved or paid |
| `E_BAD_COMMAND` | I didn't understand that. Try @countersign help. |

Layer-1 refusals are written by Switch, not Countersign, so their wording is Switch's. The README shows a screenshot and explains it.

## 6. Messages in `#ap-intake` (thread on the proposal message)

| Event | Text |
|---|---|
| Accepted | `📨 Sent for approval as {CS-id} in #ap-approvals · needs {k} approval(s){ + callback}` |
| Refused | `⛔ REFUSED · {plain reason} ({CODE}) · receipt #{seq}` |
| Superseding | `📨 Sent for approval as {new id}. This replaces {old id}, which is now void.` |
| Autopilot paid | `🛡️ AUTOPILOT PAID · {amount} to ****{last4} under {AP-id} · receipt #{seq}` |

Refusal plain reasons: `E_UNKNOWN_VENDOR` "vendor {name} isn't in vendors.yaml; add it there first" · `E_EVIDENCE_MISMATCH` "the PDF doesn't show the proposed {field}" · `E_PDF_UNREADABLE` "couldn't read text from the PDF" · `E_BAD_PROPOSAL` "the proposal block is missing or invalid: {first error}" · `E_NOT_PROPOSER` "only listed proposer agents can propose payments" · `E_NO_SOURCE` "couldn't find the message that carried this invoice".

## 7. Receipts (`#ap-audit`, top-level)

```
🧾 RECEIPT #14 · PAID · CS-7f3a2c
ACME Office Supplies · $2,480.00 USD → ****9087
Approved by meera, arjun · callback meera · initiator riya
Policy 5d1e… · 2026-11-06T08:44:12Z
digest 9c41…a2f0 · prev 3b7d…19ce
```

Other receipt types use the same 5-line shape; line 2–3 vary:
- `REFUSED`: line 2 = what was refused, line 3 = code.
- `AUTOPILOT_GRANTED`: line 2 = rule summary, line 3 = granted by.
- `AUTOPILOT_REVOKED`: line 2 = rule, line 3 = trigger (flag or person).
- `FROZEN` / `UNFROZEN` / `POLICY_*`: line 2 = reason, line 3 = old → new policy hash.

## 8. Autopilot and policy cards (`#ap-approvals`, top-level)

Autopilot proposal:
```
🛡️ AUTOPILOT PROPOSED · AP-41c0d2 · needs 2 approvals

Northwind Logistics (V-NORTH) has 2 clean payments to ****7731 (median $2,175.00).
**Rule** pay invoices up to $2,400.00 to ****7731 without approvals
**Limits** $10,000.00 total · ends 2026-12-06 · stops at the first warning flag
Reply: `@countersign approve AP-41c0d2` · `@countersign reject AP-41c0d2 <reason>`
```

Revoked (thread reply on the rule card and a receipt):
`🛡️ AUTOPILOT REVOKED · AP-41c0d2 · trigger: BANK CHANGED on INV-1048 (CS-…). Back to full approvals.`
By an approver (`autopilot revoke`): `🛡️ AUTOPILOT REVOKED · AP-41c0d2 · trigger: revoked by arjun. Back to full approvals.`
Granted (thread reply on the rule card): `🛡️ AUTOPILOT ACTIVE · AP-41c0d2 · Northwind · up to $2,400.00 · $0.00 of $10,000.00 used · ends 2026-12-06`

Frozen banner:
```
🧊 FROZEN · payments are paused
Reason: policy.yaml changed since it was last ratified (E_POLICY_UNRATIFIED).
Ratify with `@countersign approve PR-9a0c11` (needs 2 approvers of the previous policy).
Changes: approvers +sam · tiers k=2 now from $5,000.00 (was $1,000.00)

Without chat ratification (this build) the `Ratify with …` line is replaced by:
`The owner can accept this change: stop Countersign, then run \`just accept-policy\`.`
```

## 9. Commands

| Command (in `#ap-approvals` unless noted) | Response |
|---|---|
| `@countersign approve <id>` | thread reply (§5) |
| `@countersign reject <id> <reason>` | thread reply |
| `@countersign callback <id>` | thread reply |
| `@countersign status` | list of OPEN proposals and ACTIVE rules, one line each: `🟡 CS-7f3a2c · ACME · $2,480.00 · 1 of 2 · callback ✓ · expires 14:20 IST` |
| `@countersign status <id>` | the card again plus approvals so far |
| `@countersign whoami` | `You are {username} ({sender id short}). Approver: yes/no.` |
| `@countersign policy` | one-screen summary of policy.yaml + its hash |
| `@countersign autopilot list` / `autopilot revoke <AP-id or vendor_id>` | rules list / revoke (approvers only) |
| `@countersign help` (any room) | the command list above, 8 lines |
| Unknown command | `I didn't understand that. Try @countersign help.` |

Commands are case-insensitive; ids accept `cs-7f3a2c`, `CS-7F3A2C` and the bare `7f3a2c`.

Unfrozen (top-level in `#ap-approvals`, when the cause is fixed or ratified and the next check passes):
`✅ ACTIVE · payments resumed · policy 5d1e…`

Startup message (posted once per start in `#ap-approvals`):
`👋 Countersign is online · policy 5d1e… · {n} approvers · addressing check ✓ · payments ACTIVE` (or `🧊 FROZEN …`).

## 10. CLI outputs (`rich`)

### 10.1 `just redteam`
```
Countersign red team · 2026-11-06 14:31 IST · 9 attacks
 #  Attack                              Actor          Layer  Expected             Result
 A1 Impersonator approves               vikram_ceo     1      Switch refusal       ✅ blocked
 A2 Agent approves in #ap-approvals     rogue          1      Switch refusal       ✅ blocked
 A3 Injected proposer approves          scripted-intake 2     E_WRONG_ROOM         ✅ blocked
 A4 Initiator self-approval             meera          2      E_INITIATOR          ✅ blocked
 A5 Duplicate approver                  arjun          2      E_DUPLICATE          ✅ blocked
 A6 Replay on paid payment              kabir          2      E_NOT_OPEN           ✅ blocked
 A7 Late approval                       arjun          2      E_EXPIRED            ✅ blocked
 A8 Payee swap after approval           riya + agents  2      E_SUPERSEDED, E_CALLBACK_REQUIRED  ✅ blocked
 A9 Rogue loosens addressing             rogue          2      E_ADDRESSING_OPEN, E_FROZEN  ✅ blocked
9/9 blocked · 0 payments made · ledger unchanged
```
A failed row shows `❌ NOT BLOCKED` in red and the final line `n/9 blocked`; exit code 1.

### 10.2 `just verify`
`✅ chain OK · 23 receipts · head 9c41…a2f0` or `❌ chain BROKEN at receipt #12 (digest mismatch)`.

### 10.3 `just bank`
Columns: `#`, `time (UTC)`, `vendor`, `account`, `amount`, `via` (`quorum`/`autopilot`), `payment id`. Footer: total paid.

### 10.4 `just status`
Same lines as the chat `status`, plus FROZEN state and reason.

## 11. README layout (judge-facing)

1. Title + one-line pitch + 20-second GIF (card → refusals → paid).
2. **The problem** (3 bullets with the sourced numbers).
3. **What breaks without Switch** (layer 1 vs layer 2 diagram from architecture.md §5.4).
4. Architecture diagram (PNG export of architecture.md §1) + the 3 rooms.
5. **Quickstart**: prerequisites, the 4 manual Switch steps with screenshots, then `just` commands.
6. **Demo walkthrough** (both paths: Claude intake, scripted intake).
7. **Red-team results** (pasted from a real `just redteam` run).
8. Policy reference (policy.yaml keys).
9. Limitations and threat model summary.
10. Prior art and credits.
11. Team, license.

## 12. Demo video (≤ 3:00, 1080p, Mattermost in light theme, browser zoom 125%)

| Time | Shot | On screen | Voice-over (short) |
|---|---|---|---|
| 0:00–0:15 | Hook | `#ap-approvals` with 4 people + 2 agents | "When AI agents and people share a chat, who gets to say *approved*?" |
| 0:15–0:30 | Problem | Title card: $3.05B BEC · Switch docs quote | "Today, only the instructions hold an agent back." |
| 0:30–0:55 | Intake | riya uploads INV-1042; Intake replies; card appears with ⚠️ flags | "The agent prepares. Countersign builds the card itself." |
| 0:55–1:20 | Refusal 1 + 2 | vikram_ceo "approve, urgent" → Switch refusal; rogue agent → Switch refusal | "Layer one is Switch: people off the list and every agent are refused by the server." |
| 1:20–1:35 | Refusal 3 | injected approve in #ap-intake → `E_WRONG_ROOM` | "Even a hijacked agent can't approve." |
| 1:35–2:00 | Pay | meera callback + approve, arjun approve → ✅ PAID; receipt in #ap-audit | "Two people, the exact payment, inside 15 minutes." |
| 2:00–2:20 | Red team | terminal `just redteam` → 9/9 | "Nine attacks. Nine blocked." |
| 2:20–2:45 | Autopilot | AP card → granted → INV-1047 autopilot paid → INV-1048 revoked | "Trust is earned, capped and revoked at the first warning." |
| 2:45–3:00 | Close | README "What breaks without Switch" + repo URL | "Countersign: agents prepare, humans sign, nobody signs alone." |

Recording setup: three browser windows logged in as riya, meera and arjun (separate browser profiles), one terminal. Record a full backup take on Nov 6 evening.

## 13. Accessibility and tone checklist

- [ ] Every state has a word, not just an emoji.
- [ ] No message relies on color.
- [ ] Ids are monospace (inline code) so they're easy to copy.
- [ ] Refusals name the rule and the fix.
- [ ] Video has burned-in captions.
