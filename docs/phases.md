# Countersign — Phases (implementation plan)

Breaks the build into phases with tasks, owners, dependencies, exit criteria and a calendar that ends at the Nov 7 2026, 20:00 IST submission (deadline 23:59 IST). Features (`CS-F…`) are defined in [PRD.md](PRD.md); components and files in [architecture.md](architecture.md); rules in [rules.md](rules.md); message formats in [design.md](design.md).

---

## 1. Phase overview

| Phase | Name | Goal | Window (IST) | Exit gate |
|---|---|---|---|---|
| P0 | Foundations + spikes | Repo, toolchain, Switch running, every Switch assumption proven or switched to its fallback | Nov 5 09:00–13:00 | 🚩 **Go/no-go G0** |
| P1 | Core engine (pure) | All decision logic + chat text, fully tested, no I/O | Nov 5 09:00 → Nov 6 10:00 (parallel) | `just test` green on `core/`, `chat/` |
| P2 | Runtime + storage | Countersign connects, stores, posts, pays, survives restarts | Nov 5 13:00 → Nov 6 12:00 | 🚩 **G1 first end-to-end paid invoice** |
| P3 | Agents, template, fixtures | Template installs the workflow; intake agents propose; personas and invoices exist | Nov 5 13:00 → Nov 6 10:00 | Template run + 9/9 proposals valid |
| P4 | Red team + hardening | 9/9 attacks blocked; CLIs; poll mode; clean-clone test | Nov 6 12:00–18:00 | 🚩 **G2 demo-able (~60%)** |
| P5 | Should features | Autopilot (CS-F20), policy ratification (CS-F21), scripted intake polish (CS-F19) | Nov 6 14:00–22:00 | Each feature's acceptance test, or cut |
| P6 | Docs, video, submission | README, video, submission form, blog | Nov 6 18:00 → Nov 7 20:00 | 🚩 **G3 submitted** |
| P7 | Stretch | CS-F22–F26 | only after G2 and only if every M and S item is green | — |

If the team starts later than Nov 5 09:00, shift P0–P2 by the delay and take it out of P5 first (see §5 cut order). Never shorten P6.

## 2. Roles and ownership

| Role | Owns (and must be able to explain) | Phases |
|---|---|---|
| **A · Runtime** | `switch/`, `app.py`, `pdf.py`, `store/`, `__main__.py` | P0 spikes S1, S2, S4, S5, S6 · P2 · P4 poll mode |
| **B · Platform** | Switch Console, template, `switch/*.yaml`, `agents/intake/`, `scripts/`, `tools/` | P0 setup + S3, S7 · P3 · P5 CS-F19 |
| **C · Core + QA** | `core/`, `chat/render.py`, `tests/`, `redteam/` | P1 · P4 red team · P5 autopilot logic |
| **D · Story** | README, `docs/media/`, video, blog, submission, fresh-clone testing | P0 repo skeleton · P6 · QA at every gate |

Smaller team:
- **2 people:** person 1 = A + B (runtime and platform); person 2 = C + D (core, tests, red team, story).
- **Solo:** order of work is P0 → P1 (quorum, canon, codes, render only) → P2 → P3 (scripted intake first, Claude intake second) → P4 → P6. Cut P5 entirely unless G2 is reached by Nov 6 16:00.

## 3. Phase details

### P0 · Foundations + spikes (Nov 5, 09:00–13:00)

**Tasks**

| ID | Task | Owner | Depends | Done when |
|---|---|---|---|---|
| P0-T1 | Create repo skeleton per architecture.md §4 (empty modules with docstrings), `pyproject.toml`, `justfile` stubs, `.gitignore`, `.env.example`, `LICENSE` (MIT) | D | — | `just install` and `just check` run (pass on empty code) |
| P0-T2 | Pin dependencies from architecture.md §3 with `uv lock` | D | T1 | `uv.lock` committed |
| P0-T3 | Install Docker Desktop + Switch Console; "Run a server on this computer"; sign in to bundled Mattermost via "Sign-in details…" | B | — | Switch expert template answers in Mattermost |
| P0-T4 | Get a registration credential into `.env.local`: a Gateway registration key, or on a Console-run local server (no Gateway UI) the server's bootstrap key (`AGENT_REGISTRATION_TOKEN` in `switch-core.env`, see `docs/media/spikes.md`) | B | T3 | credential saved, chmod 600 |
| P0-T5 | Throwaway spike script registers a test agent and connects (spikes below) | A | T4 | spike log saved to `docs/media/spikes.md` |
| P0-T6 | Create personas by hand in Mattermost (open server) for the spikes: meera, arjun, vikram_ceo | B | T3 | 3 logins work |

**Spikes** (each has a pass criterion and a pre-decided fallback; no spike may end "unknown")

| Spike | Question | Pass criterion | Fallback if it fails | Owner |
|---|---|---|---|---|
| S1 | Is the WebSocket connection stable? | Connected 10 min with pongs, no `heartbeat_lapsed` | `COUNTERSIGN_MODE=poll` (read_context every 2 s) becomes the default | A |
| S2 | Does `filter=addressed` deliver `@countersign …` from a person, and what fields arrive? | Event logged with `sender`, `sender_name`, `timestamp`, `thread_id`, `message_id` | `filter=all` and filter for the mention ourselves | A |
| S3 | Can Gateway "Custom rules" express rule R1 (room-scoped users, `agents: []`)? Does Switch refuse the rogue agent and vikram_ceo visibly? **Result (2026-11-05): PASS.** Set through the Gateway API (`PUT /gateway/agents/{id}/addressing-policy`), with `users` = the approvers' `list_participants` ids (not usernames). | Both refused by Switch; meera's message delivered | Use built-in addressing `anyone_in: [#ap-approvals]` + layer 2 only; README states layer 1 is partial; demo shows layer-2 codes instead | B |
| S4 | Can a custom agent call ops `connect_to_room` + `post_message(thread_id)` across 3 rooms? | Thread reply appears under a card in another room after switching | Top-level posts via `POST /agents/{id}/message`, each prefixed with the short id | A |
| S5 | Does `read_context` return the server sender of a human message, and does media download return the PDF bytes? | Sender matches the uploader; PDF bytes saved | Initiator = sender of the `@intake` message the proposer quotes **and** require that message to be found in history; if history is unavailable → refuse `E_NO_SOURCE` (fail closed) | A |
| S6 | Does `load_internal_documents` return room doc content for a custom agent? | `policy.yaml` text returned | Read policy and vendors from `switch/*.yaml` on disk; policy hash still enforced; README says so | A |
| S7 | Does a v2 template create the group, 3 rooms, links, 2 docs and the `intake` agent on Mattermost? | All created in one run | Run 3 small templates (one per room) + link via ops `link_rooms`; create docs via `create_room_document` | B |

**Gate G0 (13:00):** each spike is ✅ pass or ✅ fallback adopted. Record the outcome table in `docs/media/spikes.md` and update PRD §10 decisions if a fallback was taken. **No-go** only if S1 *and* poll mode both fail (no way to receive messages). Then post in the Switch Discord immediately and keep building P1 while waiting.

### P1 · Core engine, pure (Nov 5 09:00 → Nov 6 10:00, owner C)

| ID | Task | Feature | Done when |
|---|---|---|---|
| P1-T1 | `core/models.py`: Proposal, Vendor, Policy, Approval, Flag, Decision, AutopilotRule (frozen pydantic) | CS-F4–F8 | mypy strict passes |
| P1-T2 | `core/codes.py`: every code in architecture.md §9 + plain reasons from design.md §5–6 | all | each code has a docstring |
| P1-T3 | `core/canon.py` + tests (key order, every field change, collision extension to 8 hex) | CS-F6 | ≥ 8 tests |
| P1-T4 | `core/policy.py`: parse/validate policy + vendors docs, policy hash, line diff for ratification | CS-F3, F21 | malformed docs → error; ≥ 6 tests |
| P1-T5 | `core/checks.py`: unknown vendor, bank change, amount anomaly, tier → k, callback | CS-F5 | fixtures table (architecture.md §8.2) reproduced in tests |
| P1-T6 | `core/evidence.py`: normalize text, match invoice no., amount (formats `2,480.00` and `2480.00`), account | CS-F5 | mismatch per field tested |
| P1-T7 | `core/identity.py`: the 6 rules of architecture.md §6.3 | CS-F10 | every identity code tested |
| P1-T8 | `core/quorum.py`: Allow / Pending / Deny with injected timestamps | CS-F8 | ≥ 20 table tests incl. reject-wins and callback |
| P1-T9 | `core/commands.py`: grammar of design.md §9 | CS-F9 | case/format variants tested |
| P1-T10 | `core/autopilot.py`: eligibility, rule terms (110% median rounded up to $100), coverage, revoke triggers | CS-F20 | INV-1041→1048 sequence passes as pure test |
| P1-T11 | `chat/render.py` + snapshot files for every template in design.md | CS-F7 | snapshots match design.md exactly |
| P1-T12 | Purity guard in `just check` (no forbidden imports in `core/`) | rules §5 | check fails on a planted import |

Exit: `just test` green; coverage on `core/` ≥ 90%.

### P2 · Runtime + storage (Nov 5 13:00 → Nov 6 12:00, owner A)

| ID | Task | Feature | Depends | Done when |
|---|---|---|---|---|
| P2-T1 | `store/schema.sql`, `db.py` (WAL, FK, migrations), `repo.py` | CS-F11, F12 | P1-T1 | tables of architecture.md §7 created |
| P2-T2 | `store/ledger.py` (insert-only, UNIQUE per proposal) + `receipts.py` (append, verify) + tamper test | CS-F11, F12 | T1 | `just verify` detects an edited row |
| P2-T3 | `switch/models.py`, `ws.py` (connect, pong-first, resume, gap, evicted), `ops.py`, `media.py` | CS-F2 | G0 | 30-min soak; restart resumes from cursor |
| P2-T4 | `switch/poster.py`: single queue, room switching, fallback to `/message` | CS-F2 | S4 | thread reply in the right room every time |
| P2-T5 | Poll mode (read_context every 2 s, dedupe) | CS-F2 | T3 | same behavior as WS on the demo path |
| P2-T6 | `app.py` startup self-checks: rooms by name, docs load, addressing check, DB | CS-F3 | T3, P1-T4 | each failure → 🧊 FROZEN banner |
| P2-T7 | Handler: proposal → initiator → PDF → checks → evidence → hash → supersede → store → card | CS-F4–F7, F13 | T3–T6, P1 | card posted ≤ 5 s for INV-1040 |
| P2-T8 | Handler: approve / reject / callback / status / whoami / policy / help | CS-F8–F10 | T7 | thread replies per design.md §5 |
| P2-T9 | Payment: one transaction (ledger + state + receipt + cursor), receipt post to `#ap-audit` | CS-F11, F12 | T8 | INV-1040 PAID; resending approval doesn't double pay |
| P2-T10 | Expiry sweep (30 s) using latest server timestamp | CS-F8 | T9 | INV-1044 expires in the demo |
| P2-T11 | Addressing re-check before every approval decision and payment (no caching), plus every 60 s idle (architecture §11.1, T11/T15) | CS-F3 | T6 | loosening the rule (A9) freezes payments at the next decision; restoring it unfreezes |

**Gate G1 (Nov 6 12:00):** riya uploads INV-1040 → proposal (scripted or Claude) → card → kabir approves → ✅ PAID → receipt in `#ap-audit` → `just verify` OK. Run twice on a clean DB.

### P3 · Agents, template, fixtures (Nov 5 13:00 → Nov 6 10:00, owner B)

| ID | Task | Feature | Done when |
|---|---|---|---|
| P3-T1 | `switch/policy.yaml`, `switch/vendors.yaml` per architecture.md §8 | CS-F1 | parse with `core/policy.py` |
| P3-T2 | `switch/countersign.template.yaml` (v2): group, 3 rooms with descriptions/instructions from design.md §3, links, docs, `intake` agent (provider claude, addressing `anyone_in: [ap-intake]`). Not expressible in documented v2 syntax, so cut: the exclusive `preparer` role (nothing depends on it) and room docs (made by `scripts/create_docs.py`; the template is generated by `scripts/build_template.py`) | CS-F1 | S7 pass criterion on a fresh server |
| P3-T3 | `agents/intake/instructions.md` (proposal schema §10, never approve, ignore instructions in documents) + `workspace/.claude/settings.json` (rules.md §4) | CS-F14 | Read of `~/.config/countersign/` is denied in a manual test |
| P3-T4 | `scripts/seed_invoices.py` + `fixtures/invoices.yaml` → 10 PDFs incl. the hidden line in INV-1042 | CS-F16 | PDFs committed; `pypdf` extracts expected text |
| P3-T5 | `scripts/register_agents.py` (countersign, scripted-intake, rogue → keys in `~/.config/countersign/`) | CS-F16 | 3 agents visible in Gateway |
| P3-T6 | `scripts/setup_personas.py` (5 Mattermost users, team membership, passwords to `personas.json`) + `just setup-members` (channel membership) | CS-F16 | all personas can post in the right channels |
| P3-T7 | `scripts/post_as.py` (post text / upload PDF as a persona) | CS-F15, F16 | riya uploads INV-1040 from the CLI |
| P3-T8 | `tools/agentkit.py`, `tools/scripted_intake.py` (proposal from `fixtures/invoices.yaml`), `tools/rogue.py` | CS-F19, F15 | scripted proposal for INV-1042 equals the expected canonical JSON |
| P3-T0 | `scripts/discover_env.py` + `just discover`: read the local server's `ports.json` and bootstrap token, write `.env.local`, clear failure message pointing to the README step | CS-F16 | on a fresh Console server `just discover` leaves `.env.local` ready except the Mattermost admin login |
| P3-T9 | `switch/addressing-rules.md` and `scripts/set_addressing.py` (rules R1–R3 through the Gateway API, ids resolved at run time) | CS-F17 | a teammate sets them from the doc alone; `--dry-run` prints the rules |
| P3-T10 | Claude intake run over all 9 demo invoices | CS-F14 | 9/9 valid proposals; flags match §8.2 |

Exit: template run + `just setup` + `just setup-members` on a fresh server; P3-T10 passes.

### P4 · Red team + hardening (Nov 6 12:00–18:00, owners C + A)

| ID | Task | Feature | Done when |
|---|---|---|---|
| P4-T1 | `redteam/attacks.py` + `run.py` (architecture.md §14, output design.md §10.1) | CS-F15 | 9/9 blocked, exit 0 |
| P4-T2 | `just verify`, `just bank`, `just status` | CS-F11, F12 | outputs match design.md §10 |
| P4-T3 | Fault drills: kill Countersign mid-approval; drop network 30 s; edit policy doc; loosen addressing | NFR-2, CS-F3 | no lost/duplicate decisions; FROZEN where expected |
| P4-T4 | Fresh-clone test on a second machine by D using only the README draft | CS-F17, G3 in PRD | first card ≤ 30 min; every snag fixed in README |
| P4-T5 | Log review: no keys, no full account numbers | FR-10 | grep of `var/logs` clean |

**Gate G2 (Nov 6 18:00) — demo-able:** the full video path (design.md §12) minus autopilot runs end to end twice on a clean DB, and `just redteam` is 9/9. If G2 is missed, cut P5 per §5 and spend the time on P4 and P6.

### P5 · Should features (Nov 6 14:00–22:00)

| ID | Task | Feature | Owner | Done when |
|---|---|---|---|---|
| P5-T1 | Autopilot integration: proposal card, grant via quorum, autopilot payment, revoke | CS-F20 | A + C | INV-1041 → 1046 → grant → 1047 autopilot paid → 1048 revoked, live |
| P5-T2 | Policy ratification card, diff, freeze/unfreeze | CS-F21 | A | edit policy doc → FROZEN → 2 approvals → active |
| P5-T3 | `just accept-policy` owner fallback (always built; tiny): stop Countersign, show the diff, confirm, write `POLICY_ACCEPTED_BY_OWNER` | D7 | A | works when P5-T2 is cut |
| P5-T4 | Scripted intake as a documented judge path in README | CS-F19 | B | README section tested by D |

### P6 · Docs, video, submission (Nov 6 18:00 → Nov 7 20:00, owner D)

| ID | Task | When | Done when |
|---|---|---|---|
| P6-T1 | README per design.md §11, with real screenshots and the real red-team table | Nov 6 18:00 → Nov 7 14:00 | reviewed by every role owner |
| P6-T2 | Architecture PNG + GIF for README | Nov 7 by 12:00 | renders on GitHub |
| P6-T3 | Backup video (full take) | Nov 6 21:00 | saved |
| P6-T4 | Final video per design.md §12, captions burned in, ≤ 3:00 | Nov 7 10:00–16:00 | uploaded (unlisted YouTube or as the form requires) |
| P6-T5 | Final `just redteam` + `just verify` on camera-ready DB; paste results into README | Nov 7 16:00 | 9/9, chain OK |
| P6-T6 | Make repo public; tag `v1.0-submission` | Nov 7 18:00 | clean clone from GitHub works |
| P6-T7 | Submit on the WeMakeDevs form: repo, video, README link, track 01 (and blog if done) | Nov 7 by 20:00 | confirmation received |
| P6-T8 | Blog post (CS-F25) from the README | Nov 7 if time | published URL added to submission |

### P7 · Stretch (only after G2 and all M/S green)
CS-F22 deploy policy example · CS-F23 EP-QUORUM vectors · CS-F24 Discord bridge · CS-F26 instructions-only baseline. Pick at most one; stop by Nov 7 12:00.

## 4. Calendar view (IST)

| Slot | A · Runtime | B · Platform | C · Core + QA | D · Story | Gate |
|---|---|---|---|---|---|
| Nov 5 09–13 | Spikes S1, S2, S4–S6 | Console, personas, spikes S3, S7 | P1-T1…T3 | Repo skeleton, uv lock, README outline | **G0 13:00** |
| Nov 5 13–18 | Store (P2-T1, T2), WS/ops (T3) | Policy/vendor docs, template (P3-T1, T2) | P1-T4…T8 | Screenshots of setup, README quickstart draft | |
| Nov 5 18–23 | Poster, self-checks, proposal handler (T4, T6, T7) | Intake instructions, seed invoices, register/personas scripts (P3-T3…T6) | P1-T9…T11 | Test the setup steps as they land | first card from a real proposal by 23:00 |
| Nov 6 09–12 | Approval + payment handlers (T8, T9) | post_as, scripted intake, rogue, addressing doc (P3-T7…T9) | P1-T10, T12; red-team skeleton | Record setup screenshots | **G1 12:00** |
| Nov 6 12–18 | Expiry, re-checks, fault drills (T10, T11, P4-T3) | Claude intake over 9 invoices (P3-T10) | Red team (P4-T1), CLIs (P4-T2) | Fresh-clone test (P4-T4) | **G2 18:00** |
| Nov 6 18–22 | Autopilot integration (P5-T1), ratification (P5-T2) if green | README judge path (P5-T4) | Autopilot tests live | README draft; backup video 21:00 | |
| Nov 7 09–12 | Bug fixes only | Fresh-server rehearsal | Final test pass | Video takes | scope freeze 12:00 |
| Nov 7 12–16 | Support video | Support video | Final red-team run | Edit video, README final | |
| Nov 7 16–20 | — | — | — | Public repo, tag, submit | **G3 by 20:00** |
| Nov 7 20–23:59 | Buffer: only re-submit fixes | | | | deadline |

## 5. Cut order (when behind schedule)

Cut from the top; each cut has a ready fallback so nothing is left half-done.

| Order | Cut | Fallback that remains |
|---|---|---|
| 1 | CS-F21 policy ratification | FROZEN on change + `just accept-policy` (P5-T3) |
| 2 | CS-F20 autopilot | Video drops the 2:20–2:45 segment; README mentions it as future work |
| 3 | CS-F19 scripted intake as judge path | Judges need Claude Code; README says so (scripted-intake still exists for red team) |
| 4 | Poll mode polish | WS only (keep poll mode only if WS failed in S1) |
| 5 | Claude intake on camera | Scripted intake on camera, disclosed in caption |
Never cut: CS-F1–F18 (Must), red team, README "What breaks without Switch", video.

## 6. Risks and fallbacks

| Risk | Likelihood | Impact | Early sign | Fallback (pre-decided) | Owner |
|---|---|---|---|---|---|
| Switch/Console setup takes > 4 h | M | High | Not running by Nov 5 12:00 | B keeps on setup + asks Switch Discord; A and C continue on pure code and a fake Switch client | B |
| WS protocol instability | M | High | S1 fails | Poll mode default | A |
| Custom rules can't express R1 | L–M | High | S3 fails | `anyone_in` + layer 2 only; README honest | B |
| Ops posting into threads fails | M | Low | S4 fails | Top-level posts with short-id prefix | A |
| Room docs unreadable by custom agent | L | Med | S6 fails | Policy from `switch/*.yaml` on disk | A |
| Template v2 fails on Mattermost (e.g. kickoff RootId bug #1158) | M | Med | S7 fails | No `kickoff`; split templates; ops for links/docs | B |
| Claude intake flaky or obeys the injection | M | Med | P3-T10 failures | Scripted intake for the demo; if it obeys, show that Countersign still refuses (that's the point) | B |
| PDF text extraction differs across generators | L | Med | P1-T6 vs P3-T4 mismatch | Tune fixtures (fpdf2 core fonts), not the matcher | C |
| Fresh-clone setup fails for judges | M | High | P4-T4 snags | Fix README; add a troubleshooting section; keep backup video | D |
| Scope creep | H | Med | Stretch started before G2 | Rules §3; P7 locked until G2 | All |
| Running late on Nov 7 | M | High | G2 missed | Cut order §5; submit by 20:00 regardless | D |

## 7. Checklists

### 7.1 MVP (must be all ✅ before G2)
- [ ] CS-F1 template installs the workflow
- [ ] CS-F2 runtime stable, restart-safe
- [ ] CS-F3 self-checks freeze on failure
- [ ] CS-F4–F7 proposal → trusted card
- [ ] CS-F8–F10 approvals, identity, quorum
- [ ] CS-F11–F12 mock bank + receipts + verify
- [ ] CS-F13 supersede
- [ ] CS-F14 intake agent restricted and accurate
- [ ] CS-F15 red team 9/9
- [ ] CS-F16 setup scripts
- [ ] CS-F17 README drafted and tested by someone else

### 7.2 Submission (G3)
- [ ] Public repo, tag `v1.0-submission`, MIT license
- [ ] README: what breaks without Switch, 4 manual steps, quickstart, red-team table from a real run, limitations, prior art
- [ ] Video ≤ 3:00 with captions; link works logged out
- [ ] No secrets in git history (`git log -p | grep -i key` reviewed)
- [ ] Track 01 selected on the form; blog URL if done
- [ ] Every role owner can explain their modules (rules.md §6)

## 8. Definition of done (every task)
Code + tests + snapshots/docs updated · `just check` and `just test` green · demo path still runs on a clean DB · owner can explain it.
