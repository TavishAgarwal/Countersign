# Autopilot run log

Live check of earned autopilot (CS-F20) against a real Switch 0.31.0 server:
`uv run python scripts/autopilot_path.py`. It uploads invoices as `riya`, lets the scripted intake
agent propose, approves as `meera`, `arjun` and `kabir`, and asserts on Countersign's database.

## Run 3 (2026-11-06, clean database, `rm -rf var` then `just run`)

Earlier runs: two clean-database runs on 2026-11-05 also passed 13/13; the first autopilot attempts
during development failed and were fixed before those runs (see git history). This is the run kept as
evidence.

```text
riya uploaded INV-1041.pdf (post fcgwk6nt6jf45nc9pi9b6yzgbe)
✅ INV-1041 paid by meera + arjun
✅ no autopilot offer after one payment
riya uploaded INV-1046.pdf (post j3d4gch3fpfkix8jawqk8r7par)
✅ INV-1046 paid by meera + arjun
✅ autopilot offered after two clean payments · AP-3d712a
✅ one approval does not grant
✅ second approval grants the rule · AP-3d712a
riya uploaded INV-1047.pdf (post 81ocqtgik3gbuesh9kb4ujhjhy)
✅ INV-1047 paid on autopilot, no approvals · +205000
riya uploaded INV-1048.pdf (post sknc1azuejgc7yckrahnr3f3qw)
✅ INV-1048 (bank changed) revokes the rule
✅ INV-1048 is not paid yet
✅ two approvals without the callback do not pay
✅ callback completes the payment
✅ receipt chain intact · 15 receipts
✅ ledger equals the four payments · 850000
13/13 steps passed
```

After the run: `just verify` prints `chain OK · 15 receipts`; `just bank` shows four payments for
$8,500.00 in total (three by quorum, INV-1047 for $2,050.00 `via=autopilot`).

## What it shows

- No offer after one clean payment; the offer (`AP-xxxxxx`) appears after two.
- One approval does not grant the rule; the second does (`grant_k: 2`).
- INV-1047, same vendor and account, within the rule, is paid with no approvals and an
  `AUTOPILOT_PAID` receipt.
- INV-1048, same vendor with a **changed bank account**, revokes the rule on the spot and gets the
  normal path: two approvals are not enough, the callback is also required.

## What it does not show

- The proposals come from `scripted-intake`, not Claude (see `claude-intake-run.md` for Claude).
- The 30-day rule expiry and the $10,000 cap are covered by unit tests, not by this live run.
- Approvals are posted by script as the three approver personas, not typed by a person.
