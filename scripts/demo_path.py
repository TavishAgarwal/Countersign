"""The demo path without autopilot, end to end against a running Countersign (fresh database).

  1. INV-1040 (clean, $480): proposal -> kabir approves -> PAID
  2. INV-1042 (changed bank, anomaly, hidden line): meera + kabir approve, arjun's callback -> PAID
  3. INV-1045 (unknown vendor): refused with E_UNKNOWN_VENDOR
  4. INV-1044 (20 s window): nobody approves -> EXPIRED
Then the receipt chain is verified. Exit 0 only when every step ends as expected.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redteam.attacks import ROOT, Ctx, SetupError

from countersign.store import receipts

PAID_EXPECTED = 48000 + 248000


def state(c: Ctx, number: str) -> str:
    row = c.proposal(number)
    return str(row["state"]) if row else "none"


def step(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'✅' if ok else '❌'} {name}{' · ' + detail if detail else ''}", flush=True)
    return ok


def main() -> int:
    c = Ctx()
    if c.proposal("INV-1040") is not None:
        print("database is not fresh: stop Countersign, `just reset`, start it again")
        return 2
    results: list[bool] = []
    try:
        p = c.propose("INV-1040", "INV-1040")
        c.approve("kabir", p["short_id"])
        ok = c.wait(lambda: state(c, "INV-1040") == "PAID", 30)
        results.append(step("INV-1040 approved by kabir -> PAID", ok, p["short_id"]))

        p = c.propose("INV-1042", "INV-1042")
        c.approve("meera", p["short_id"])
        time.sleep(1)
        c.approve("kabir", p["short_id"])
        time.sleep(4)
        held = state(c, "INV-1042") == "OPEN"
        results.append(step("INV-1042 two approvals, no callback yet -> still OPEN", held))
        c.say("arjun", "ap-approvals", f"@countersign callback {p['short_id']}")
        ok = c.wait(lambda: state(c, "INV-1042") == "PAID", 30)
        results.append(step("INV-1042 callback by arjun -> PAID", ok, p["short_id"]))

        before = c.max_seq()
        import asyncio

        from post_as import demo_upload

        from countersign.tools import scripted_intake

        demo_upload("INV-1045", mention=False)
        time.sleep(2)
        asyncio.run(scripted_intake.run("INV-1045", ROOT))
        ok = c.wait(lambda: any(
            b.get("code") == "E_UNKNOWN_VENDOR" for b in c.receipts_after(before, "REFUSED")
        ), 20)  # fmt: skip
        results.append(step("INV-1045 unknown vendor -> REFUSED E_UNKNOWN_VENDOR", ok))

        p = c.propose("INV-1044", "INV-1044")
        ok = c.wait(lambda: state(c, "INV-1044") == "EXPIRED", 70)
        results.append(step("INV-1044 not approved -> EXPIRED", ok, p["short_id"]))
    except SetupError as exc:
        print(f"setup failed: {exc}")
        return 2
    chain = receipts.verify(c.db)
    results.append(step("receipt chain intact", chain.ok, f"{chain.count} receipts"))
    total = c.ledger_total()
    results.append(
        step("ledger equals the two approved payments", total == PAID_EXPECTED, str(total))
    )
    print(f"{sum(results)}/{len(results)} steps passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
