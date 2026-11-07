"""The autopilot demo path against a running Countersign (fresh database), scripted intake.

  INV-1041 and INV-1046 (Northwind, clean): each paid by two approvals
  -> Countersign offers autopilot AP-xxxxxx; meera and arjun grant it
  INV-1047 -> paid on autopilot, no approvals
  INV-1048 (bank changed) -> autopilot revoked; full approvals and a callback needed, then paid
Exit 0 only when every step ends as expected.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redteam.attacks import Ctx, SetupError

from countersign.store import receipts

TOTAL = 215000 + 220000 + 205000 + 210000


def step(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'✅' if ok else '❌'} {name}{' · ' + detail if detail else ''}", flush=True)
    return ok


def state(c: Ctx, number: str) -> str:
    row = c.proposal(number)
    return str(row["state"]) if row else "none"


def grant(c: Ctx) -> object:
    return c.db.execute(
        "SELECT * FROM proposals WHERE kind='autopilot_grant' ORDER BY opened_at_ms DESC"
    ).fetchone()


def rule_state(c: Ctx, rule_id: str) -> str:
    row = c.db.execute("SELECT state FROM autopilot_rules WHERE rule_id=?", (rule_id,)).fetchone()
    return str(row["state"]) if row else "none"


def pay(c: Ctx, inv: str, approvers: tuple[str, ...]) -> bool:
    row = c.propose(inv, inv)
    for who in approvers:
        c.approve(who, row["short_id"])
        time.sleep(1)
    return c.wait(lambda: state(c, inv) == "PAID", 30)


def main() -> int:
    c = Ctx()
    if c.proposal("INV-1041") is not None:
        print("database is not fresh: stop Countersign, `just reset`, start it again")
        return 2
    results: list[bool] = []
    try:
        results.append(
            step("INV-1041 paid by meera + arjun", pay(c, "INV-1041", ("meera", "arjun")))
        )
        results.append(step("no autopilot offer after one payment", grant(c) is None))
        results.append(
            step("INV-1046 paid by meera + arjun", pay(c, "INV-1046", ("meera", "arjun")))
        )
        offered = c.wait(lambda: grant(c) is not None, 20)
        g = grant(c)
        rid = str(g["short_id"]) if g else ""
        results.append(step("autopilot offered after two clean payments", offered, rid))
        c.approve("meera", rid)
        time.sleep(1)
        results.append(step("one approval does not grant", rule_state(c, rid) == "PROPOSED"))
        c.approve("arjun", rid)
        results.append(step("second approval grants the rule",
                            c.wait(lambda: rule_state(c, rid) == "ACTIVE", 20), rid))  # fmt: skip
        before = c.ledger_total()
        c.propose("INV-1047", "INV-1047")
        ok = c.wait(lambda: state(c, "INV-1047") == "PAID", 20)
        via = c.db.execute("SELECT via FROM ledger ORDER BY id DESC LIMIT 1").fetchone()["via"]
        paid_by_rule = ok and via == "autopilot"
        gained = c.ledger_total() - before
        results.append(step("INV-1047 paid on autopilot, no approvals", paid_by_rule, f"+{gained}"))
        c.propose("INV-1048", "INV-1048")
        revoked = c.wait(lambda: rule_state(c, rid) == "REVOKED", 20)
        results.append(step("INV-1048 (bank changed) revokes the rule", revoked))
        results.append(step("INV-1048 is not paid yet", state(c, "INV-1048") == "OPEN"))
        row = c.proposal("INV-1048")
        c.approve("meera", row["short_id"])
        time.sleep(1)
        c.approve("kabir", row["short_id"])
        time.sleep(4)
        results.append(step("two approvals without the callback do not pay",
                            state(c, "INV-1048") == "OPEN"))  # fmt: skip
        c.say("arjun", "ap-approvals", f"@countersign callback {row['short_id']}")
        results.append(step("callback completes the payment",
                            c.wait(lambda: state(c, "INV-1048") == "PAID", 30)))  # fmt: skip
    except SetupError as exc:
        print(f"setup failed: {exc}")
        return 2
    chain = receipts.verify(c.db)
    results.append(step("receipt chain intact", chain.ok, f"{chain.count} receipts"))
    results.append(
        step("ledger equals the four payments", c.ledger_total() == TOTAL, str(c.ledger_total()))
    )
    print(f"{sum(results)}/{len(results)} steps passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
