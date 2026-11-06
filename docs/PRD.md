# Countersign — Product Requirements Document

| | |
|---|---|
| **Product** | Countersign: agents prepare, humans sign, nobody signs alone |
| **Hackathon** | Flip the Switch (WeMakeDevs × DevTools Academy × Switch), Nov 4–7 2026 |
| **Track** | 01 · Best Business Workflow (side entry: Best Blog) |
| **Deadline** | Nov 7 2026, 23:59 IST · internal freeze 20:00 IST |
| **Status** | Planned, not built · version 1.0 · 2026-11-05 |
| **Related docs** | [architecture.md](architecture.md) · [rules.md](rules.md) · [phases.md](phases.md) · [design.md](design.md) |
| **Source research** | `blaze/idea/IDEA.md`, `blaze/idea/notes/*` (track choice, prior art, panel scores, code verification) |

---

## 1. Summary

Countersign is a Switch agent that holds the only power to pay invoices. In a shared team chat, an AI agent reads invoices and *proposes* payments. Countersign pays only when **k distinct, listed humans** approve the **exact** payment within a time window. An agent cannot approve, and the person who submitted the invoice cannot approve it.

Two layers enforce this:

1. **Switch (layer 1).** Countersign's Switch addressing policy admits only the listed approvers in `#ap-approvals`, and no agents. Switch's server refuses everyone else before Countersign sees the message.
2. **Countersign (layer 2).** It checks the quorum, the initiator, the action hash, the time window and the invoice evidence, and fails closed on any doubt.

Routine vendors can earn a bounded **autopilot**. It is granted only by a quorum and revoked on the first anomaly.

## 2. Problem

- Business email compromise (BEC) caused about **$3.05B** in reported US losses in 2025 (FBI IC3 2025, secondary summary: https://www.mcdonaldhopkins.com/insights/news/the-sobering-truth-of-the-fbis-2025-internet-crime-complaint-center-report).
- 79% of organizations faced attempted or actual payments fraud in 2024 (AFP 2025 survey press release).
- Teams now put AI agents in the same chat rooms as people. In a shared room, **any participant can type "approved"**, including an agent that read a poisoned invoice.
- Switch's docs say business approvals are not enforced: "Only the instructions hold it back" (https://docs.switchagents.ai/switch-rooms/use-cases/incident-response.md).
- Research: an approval is a real second factor only if the agent cannot produce it and it names exactly what it authorizes. Agent-written summaries fool approvers (54% attack success without trusted rendering, https://arxiv.org/abs/2610.21063).

## 3. Goals and non-goals

### Goals
| ID | Goal | Measure |
|---|---|---|
| G1 | No payment without k distinct listed humans approving the exact payment | 9/9 red-team attacks refused; 0 payments in the ledger without a valid quorum or active autopilot rule |
| G2 | Switch is load-bearing | Layer 1 (addressing policy) visibly refuses agent and non-approver approvals in the demo |
| G3 | A judge can reproduce it | Fresh clone → first approval card in ≤ 30 min following the README, including a path **without** a Claude subscription |
| G4 | Clear 3-minute story | Video shows the BEC invoice, 3 refusals, a payment, a receipt, and autopilot grant and revoke |
| G5 | Score ≥ 75/100 on the official rubric | Use of Switch 30 · Technical 25 · Creativity & impact 25 · Presentation 20 |

### Non-goals (explicitly out of scope)
- Real money movement, real banks, Stripe or any payment provider. Payments go to a **mock ledger**.
- Vendor onboarding. An unknown vendor is refused, not created.
- OCR of scanned invoices. Seed invoices are text PDFs.
- Multi-currency conversion. All amounts are USD.
- Certified SOX/financial controls. This is a prototype of the pattern.
- A web UI. The chat is the UI; there are CLI tools for audit.
- Slack, Teams, Discord or Telegram bridges in the MVP. Mattermost (bundled) only.
- Multi-tenant hosting. Single local Switch server.

## 4. Target users and personas

**Primary user:** finance and AP teams of 5–200 people who use or want AI agents to prepare payments in team chat.
**Secondary user:** platform or security engineers who want a reusable "two-person rule for agents" on Switch.
**Hackathon audience:** the WeMakeDevs, Switch and DevTools Academy judges.

Demo personas (test accounts on the bundled Mattermost):

| Persona | Username | Role in workflow | On approver list? | In which rooms |
|---|---|---|---|---|
| Riya Shah | `riya` | AP clerk, uploads invoices (initiator) | No | `#ap-intake`, `#ap-approvals` (read), `#ap-audit` |
| Meera Iyer | `meera` | Controller, approver, does vendor callbacks | Yes | all 3 |
| Arjun Mehta | `arjun` | CFO, approver | Yes | all 3 |
| Kabir Khan | `kabir` | Finance manager, approver (third approver, so 2-of-3 works when one is away) | Yes | all 3 |
| "Vikram Rao (CEO)" | `vikram_ceo` | Lookalike impersonator account (BEC attacker) | No | `#ap-approvals` |
| Switch admin | (Console admin login) | Owner of both agents; installs the template; **not** an approver | No | all 3 |

Agents:

| Agent | Kind | Owner | Can do | Cannot do |
|---|---|---|---|---|
| `intake` | Claude Code via Switch (provider `claude`) | Switch admin | Read invoice PDFs, check the vendor list, post proposals to Countersign | Pay, approve, write files, use the shell or the network |
| `countersign` | Custom Python agent (Switch agent protocol) | Switch admin | Validate, collect approvals, pay the mock ledger, post receipts | Be addressed by agents in `#ap-approvals`; use an LLM in decisions |
| `scripted-intake` | Custom Python agent (no LLM) | Switch admin | Same proposals as `intake`, scripted, for judges without Claude | Anything else |
| `rogue` | Custom Python agent (red team only) | Switch admin | Try to approve in `#ap-approvals` | Succeed |

## 5. User stories

| ID | As a… | I want… | So that… |
|---|---|---|---|
| US-1 | AP clerk | to drop an invoice in chat and have an agent prepare the payment | I don't type payment details by hand |
| US-2 | Approver | a card showing exactly what will be paid, built by a component that isn't an LLM | I'm not misled by an agent's summary |
| US-3 | Approver | to approve with one chat message | approval is fast where the work happens |
| US-4 | Controller | bank-detail changes to require a callback to the number on file | BEC bank swaps are caught |
| US-5 | CFO | payments ≥ $1,000 to need two of us | no single person can move large sums |
| US-6 | Anyone | urgent requests from anyone not on the list to be refused | pressure doesn't bypass the control |
| US-7 | Security engineer | agents in the room to be unable to approve, even if prompt-injected | a compromised agent can't move money |
| US-8 | Auditor | a tamper-evident receipt for every decision | I can verify after the fact |
| US-9 | Finance manager | routine vendors to go on capped autopilot once trust is earned | we don't approve the same $2k invoice every week forever |
| US-10 | Finance manager | autopilot to switch itself off at the first anomaly | autopilot can't be abused quietly |
| US-11 | Owner/admin | policy edits to freeze payments until approvers ratify them | an admin can't quietly loosen the rules |
| US-12 | Judge | to reproduce the demo from the README, even without a Claude subscription | I can verify it runs |

## 6. Features and acceptance criteria

Priority: **M** = Must (MVP, blocks submission) · **S** = Should (MVP if on schedule; first to cut, in the order of the "cut order" column) · **C** = Could (stretch).

| ID | Feature | Pri | Cut order | Acceptance criterion (testable) |
|---|---|---|---|---|
| CS-F1 | **Switch template** `countersign.template.yaml`: room group with `#ap-intake`, `#ap-approvals`, `#ap-audit`, links intake→approvals→audit, agent `intake`, room docs `policy.yaml` and `vendors.yaml`, agent role `preparer` (exclusive) | M | — | Running the template on a fresh local server creates 3 Mattermost channels, 3 Switch rooms, 2 links, 2 docs, the `intake` agent; `!list-agents` in `#ap-intake` lists `intake` and `countersign` |
| CS-F2 | **Countersign agent runtime**: register, WebSocket connection (`scope=all`, `filter=addressed`), ping/pong, reconnect and resume from a stored cursor, ops client, one ordered posting queue | M | — | Runs 30 min with no lapse; after a kill and restart it resumes without reprocessing (no duplicate replies); `@countersign help` answered in ≤ 3 s |
| CS-F3 | **Startup self-checks** (fail-closed): rooms found by name; policy doc and vendor doc load and validate; addressing policy fetched and verified as not open and admitting no agents in `#ap-approvals`; database schema OK | M | — | Each failed check posts a 🧊 FROZEN banner with the reason code in `#ap-approvals` and blocks payments; `status` shows FROZEN |
| CS-F4 | **Proposal intake**: parse the proposal block from the listed proposer agent in `#ap-intake`; resolve the initiator from the server's record of the source message; download the PDF; validate fields | M | — | Valid proposal → card posted in ≤ 5 s; proposal from any other sender → `E_NOT_PROPOSER`; missing source message → `E_NO_SOURCE` |
| CS-F5 | **Deterministic checks**: vendor known; bank details vs vendor list; amount vs vendor baseline (> 2× → anomaly); evidence check (invoice number, amount and account number appear in the PDF text) | M | — | Fixture tests: unknown vendor → `E_UNKNOWN_VENDOR`; mismatched amount → `E_EVIDENCE_MISMATCH`; bank change → flag `BANK_CHANGED`; 3× amount → flag `AMOUNT_ANOMALY` |
| CS-F6 | **Canonical action hash**: sorted-key compact JSON, integer cents, normalized account fields, includes policy hash; `action_hash = sha256`; short id `CS-` + first 6 hex | M | — | Unit tests: key order doesn't change the hash; any change to amount, account, vendor, invoice number or policy changes it |
| CS-F7 | **Trusted approval card** in `#ap-approvals`, rendered only from the canonical proposal and Countersign's own checks; agent notes shown in a separate, labelled "Intake says" line | M | — | Card snapshot tests match [design.md §4](design.md); no agent free text outside the labelled line |
| CS-F8 | **Quorum engine** (pure function): tiers (< $1,000 → k=1; ≥ $1,000 → k=2), bank change → k=2 plus callback, distinct approvers, initiator excluded, inside window (server timestamps), any reject → rejected, unknown or malformed → deny | M | — | ≥ 20 table tests pass, including every reason code in [architecture.md §9](architecture.md) |
| CS-F9 | **Approval commands** in `#ap-approvals`: `approve`, `reject <reason>`, `callback`, `status`, `whoami`, `policy`, `help` | M | — | Command parser tests; each command replies in the card's thread per [design.md §5](design.md) |
| CS-F10 | **Identity checks (layer 2)**: sender name on the approver list; `list_participants` shows that name as a `user` and not as an agent; sender id pinned on first sight and must match afterwards; message must be in `#ap-approvals` | M | — | Tests: agent-named sender → `E_NOT_HUMAN`; same name with a new sender id → `E_IDENTITY_CHANGED`; approval posted in `#ap-intake` → `E_WRONG_ROOM` |
| CS-F11 | **Mock bank + payment execution**: only Countersign writes the ledger; idempotent per proposal | M | — | `just bank` lists exactly the paid proposals; re-sending the final approval doesn't pay twice |
| CS-F12 | **Hash-chained receipts** in `#ap-audit` and SQLite; `just verify` re-checks the chain | M | — | `just verify` → "chain OK (n receipts)"; hand-editing one row → "chain BROKEN at #i" |
| CS-F13 | **Supersede on change**: a new proposal for the same vendor + invoice number voids the open one; old approvals no longer count | M | — | Test: 1 approval, then a re-proposal with a new account → old card gets ⚪ VOID reply, new card needs fresh approvals and a callback |
| CS-F14 | **Intake agent** (Claude Code) instructions + workspace permissions (Read only; no Bash, Write, Edit, web; Read denied on Countersign's key and database) | M | — | On the 9 demo invoices: valid proposal JSON 9/9; flags match the fixture table in [architecture.md §8](architecture.md) |
| CS-F15 | **Red-team suite** `just redteam`: 9 live attacks, prints a results table, exits non-zero if any attack succeeds | M | — | 9/9 refused with the expected layer and code |
| CS-F16 | **Seed and setup scripts**: test personas on Mattermost, channel membership, 10 generated invoice PDFs, `.env.local` with generated test credentials | M | — | `just setup` + `just setup-members` on a fresh server complete with no manual steps beyond the 4 listed in the README (Mattermost admin login in `.env.local`, template run, `!invite-agent`, `just set-addressing` with the Gateway admin password; `just discover` writes the rest of `.env.local` and fails with a message pointing to the README if Switch Console's files are not found) |
| CS-F17 | **README**: what it is, what breaks without Switch, architecture diagram, setup, demo walkthrough, red-team table, limitations | M | — | A teammate who didn't build it reaches the first card using only the README |
| CS-F18 | **Demo video** ≤ 3 min + backup recording | M | — | Follows the shot list in [design.md §12](design.md) |
| CS-F19 | **Scripted intake agent** (no LLM) for judges without Claude | S | 3 | `just scripted-intake INV-1042` produces the same proposal as the Claude intake for that fixture |
| CS-F20 | **Earned autopilot**: after `clean_runs` (=2) clean quorum-paid invoices for one vendor (same account, amount within ±10% of the median), propose a rule {vendor, max amount, 30-day expiry, $10k cumulative cap}; grant needs k=2; autopilot pays within limits; any anomaly revokes it | S | 2 | Fixture sequence INV-1041 → 1046 → grant → 1047 paid on autopilot → 1048 (bank change) → 🛡️ REVOKED + full quorum + callback |
| CS-F21 | **Policy ratification**: a changed policy doc freezes payments; `ratify-policy` needs k=2 under the previous policy | S | 1 | Edit the policy doc → 🧊 FROZEN; 2 approvals → active with the new policy hash; receipts record the policy hash |
| CS-F22 | Same engine, second domain: a `deploy` policy example | C | — | One demo clip |
| CS-F23 | EP-QUORUM conformance vectors run against the quorum engine | C | — | Pass, or each divergence documented |
| CS-F24 | Discord bridge | C | — | Same flow in a Discord channel |
| CS-F25 | Blog post "Your agent can't approve its own payment" (Best Blog side track) | C | — | Published URL in the submission |
| CS-F26 | "Instructions-only" baseline agent for a side-by-side attack table | C | — | Same 9 attacks; table shows which succeed against the baseline |

## 7. Functional requirements (rules the system must follow)

| ID | Requirement |
|---|---|
| FR-1 | Countersign is the only component that can write the payment ledger. |
| FR-2 | No LLM call anywhere in Countersign's decision path. |
| FR-3 | A payment executes only when (a) the quorum predicate returns Allow for that exact action hash, or (b) an active autopilot rule covers it and every deterministic check passes. |
| FR-4 | Approvals count only from messages in `#ap-approvals` that address Countersign, from a sender on the approver list who passes every identity check in CS-F10. |
| FR-5 | The initiator is the server-recorded sender of the message that carried the invoice, never a field the agent supplied. The initiator's approval never counts. |
| FR-6 | Time checks use Switch's server timestamps on events, not the local clock. |
| FR-7 | Any exception, malformed input, missing data or failed self-check means **deny / freeze**, never allow. |
| FR-8 | Every decision (paid, refused, rejected, expired, void, autopilot grant/use/revoke, policy freeze/ratify) produces a hash-chained receipt. |
| FR-9 | Each incoming message is processed exactly once (dedupe by message id), in order, by one consumer. |
| FR-10 | Countersign never echoes secrets, tokens or full account numbers into chat (account numbers are masked to the last 4 digits). |
| FR-11 | Unknown vendors are refused; vendor records change only by editing the vendor doc, which counts as a policy change (CS-F21). |
| FR-12 | All money is integer cents (USD). No floats. |

## 8. Non-functional requirements

| ID | Area | Requirement |
|---|---|---|
| NFR-1 | Latency | Card ≤ 5 s after the proposal; approval reply ≤ 3 s |
| NFR-2 | Reliability | Survives Countersign restart and WebSocket drops with no lost or duplicated decisions |
| NFR-3 | Reproducibility | One-command setup after the 3 manual Switch steps; pinned dependencies (`uv.lock`) |
| NFR-4 | Security | Secrets only in `.env.local` / `~/.config/countersign/` (chmod 600), never in git; Intake can't read them |
| NFR-5 | Testability | Core logic is pure (no I/O) with ≥ 90% line coverage on `core/` |
| NFR-6 | Portability | macOS (primary, Console supported); Linux best effort; Windows not supported (Console early access) |
| NFR-7 | Accessibility | Every state shown in chat has a text word, not only an emoji |
| NFR-8 | Chat limits | Each Countersign message ≤ 1,500 characters; no Markdown tables in chat |

## 9. Hackathon compliance

| Requirement (official) | How Countersign meets it |
|---|---|
| ≥ 1 AI agent and ≥ 1 person complete a real task together in team chat via Switch | Intake (Claude Code) + Countersign + 4 people pay invoices in Mattermost through Switch rooms |
| Switch central; "would it stop working without it?" | Layer-1 enforcement is Switch's addressing policy; rooms, links, docs, template and agent protocol are all Switch. Without Switch there's no server-side identity, so approvals become plain text. |
| Switch setup and workflow built during the hackathon | All code, template and setup written Nov 4–7 (git history shows it) |
| Runs; clone and reproduce | `just setup`, scripted-intake path, pinned deps, README tested on a second machine |
| README shows Switch use; demo video | CS-F17, CS-F18 |
| Can explain the submission; AI tools allowed | rules.md §6: every module has an owner who can explain it |

## 10. Decisions log (every open question, closed)

| # | Question | Decision | Why |
|---|---|---|---|
| D1 | Track | Track 01 Business | `blaze/idea/notes/track-decision.md` |
| D2 | How do we tell humans from agents? | Layer 1 Switch addressing policy (server resolves sender kind; `agents: []` in `#ap-approvals`). Layer 2: approver list by username + `list_participants` type `user` + pinned sender id | Events don't carry user/agent kind to custom agents (`message_events.py:63-66`); addressing does (`addressing.py`) |
| D3 | Do we need Switch user accounts for each persona? | No. `sender_role` hardening is optional (`require_workspace_role: false`) | Personas are Mattermost-only test accounts |
| D4 | Who is the initiator? | Server-recorded sender of the `source_message_id` read via `read_context` | Agent-supplied fields are untrusted |
| D5 | Which clock? | Switch event timestamps (ms) | Local clocks can drift or be changed |
| D6 | Can the agent lie about the invoice? | Countersign downloads the PDF itself and checks invoice number, amount and account appear in its text | Trusted rendering needs ground truth |
| D7 | Where does policy live? | Switch room doc `policy.yaml` (owned by the template creator). Changes freeze payments until ratified (S) or until restart with `--accept-policy` (fallback if CS-F21 is cut) | Switch-native, and editing it isn't a silent bypass |
| D8 | Window | 900 s by default; per-vendor override (fixture vendor `V-QUICK` = 20 s for the expiry attack) | Real teams set shorter windows for risky vendors |
| D9 | Tiers | < $1,000 → k=1; ≥ $1,000 → k=2; bank change → k=2 + callback; unknown vendor → refuse | Simple, demo-able, realistic |
| D10 | Callback | `callback CS-…` from a listed approver (not the initiator) after calling the number in the vendor doc; may be one of the 2 approvers | Mirrors real AP controls |
| D11 | Autopilot | `clean_runs: 2`, band ±10% of the median, max amount = 110% of the median rounded up to $100, 30-day expiry, $10k cap; grant k=2; revoke on any flag, evidence mismatch or account change | Keeps the demo short, the rule bounded |
| D12 | Payment rail | SQLite mock ledger inside Countersign | No real money; reproducible |
| D13 | Posting into threads | Ops `post_message(thread_id)` through one posting queue that calls `connect_to_room` on room change; fallback: top-level posts via `POST /agents/{id}/message` prefixed with the short id | `/message` endpoint has no threads (`handlers.py:487`) |
| D14 | Setting the addressing rule | `scripts/set_addressing.py` calls the Gateway API (`PUT /gateway/agents/{id}/addressing-policy`) as the admin (manual README step: the admin enters the Gateway password); Countersign verifies it at startup | Console-run local servers have no Gateway UI, but serve the API at `/gateway` (Swagger at `/gateway/docs`). Ops `update_agent_detail` only offers 3 presets. `users` entries are the approvers' participant ids from `list_participants`; usernames match nobody (spike S3) |
| D15 | Judges without Claude | `scripted-intake` custom agent (S) produces the same proposals | Reproducibility is 25% of the score |
| D16 | Currency and bank format | USD; `account_number` + `routing_number` strings; masked to last 4 in chat | Avoids IBAN/IFSC mix |
| D17 | Invoices | 10 generated text PDFs (9 demo + 1 red-team re-issue; fixture table in architecture.md §8.2); INV-1042 has a labelled hidden prompt-injection line | Deterministic demo |
| D18 | Intake key isolation | Intake: Read/Glob/Grep + Switch tools only; Claude Code `permissions.deny` on Countersign's key and DB paths; Countersign key in `~/.config/countersign/` chmod 600 | Even a fully injected Intake can't pay or exfiltrate |
| D19 | Bridges | Mattermost only in MVP | Bundled; no admin access needed |
| D20 | License | MIT for our code; Switch stays a dependency, not vendored | Switch is Apache 2.0 + Commons Clause |
| D22 | Registration credential and room setup on a Console-run local server | No Gateway UI, so no registration-key page: use the server's bootstrap key (agents are then owned by a dedicated non-admin account) or create a key with `POST /gateway/api-keys` after Gateway login. Rooms may be created through ops `create_room` / `create_room_from_yaml`; room docs through `create_room_document`; the `intake` agent (needs "can manage agents") is created by the human running the template in Console | Spikes S1–S7, `docs/media/spikes.md` |
| D23 | Same-owner agents (spike S3 follow-up) | On a Console-run local server all agents registered with the bootstrap key share one owner, and any same-owner agent can change another's addressing (`update_agent_detail`). Countersign therefore re-reads its own addressing policy before **every** approval decision and payment (not every 5 min) and freezes on any loosening (threat T15, attack A9). Rule R3 names the Switch admin's participant id instead of `owner` (the owner is a bootstrap account, not a person). `just discover` finds the local ports and bootstrap token and writes `.env.local` | Review of spikes S3/S7; shared-owner hole; reproducibility (G3) |
| D21 | Track 03 | No planned PR; open one only for a real bug found while building, on a low-crowd issue, ≤ 4 h | Low EV, crowded |

## 11. Success metrics (demo day)

- 9/9 red-team attacks refused (layer and code shown).
- 0 ledger rows without a matching quorum or autopilot receipt (`just verify`).
- Intake proposals valid on 9/9 fixtures; flags match the fixture table.
- Fresh-clone setup ≤ 30 min (timed by someone who didn't build it).
- Video ≤ 3:00.

## 12. Risks (summary; full table with fallbacks in phases.md §6)

1. Gateway "Custom rules" can't express the rule we need → narrowest built-in option + layer 2 only, stated in README.
2. WebSocket protocol trouble → HTTP poll mode (read_context every 2 s).
3. Switch setup takes too long → one person on setup, others on pure core code.
4. Claude intake is flaky → scripted intake for the demo, Claude intake shown once.
5. Scope → strict cut order (CS-F21, then CS-F20, then CS-F19).

## 13. Glossary

| Term | Meaning |
|---|---|
| Proposal | A payment the intake agent asks for; identified by its action hash |
| Action hash | sha256 of the canonical proposal JSON; short form `CS-xxxxxx` |
| Quorum | k distinct listed humans approving the same action hash inside the window |
| Initiator | The person whose message carried the invoice |
| Callback | An approver confirms a bank change by calling the number on file |
| Autopilot rule | A quorum-granted, capped, expiring permission to pay one vendor without approvals; id `AP-xxxxxx` |
| Layer 1 / Layer 2 | Switch's addressing policy / Countersign's own checks |
| Frozen | Payments blocked until the cause is fixed or ratified |
