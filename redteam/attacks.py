"""The nine attacks (architecture.md §14). Each returns (blocked, detail); nothing is mocked.

The suite acts through the same doors an attacker has: Mattermost posts as personas, agent posts
through Switch, and one ops call by the `rogue` agent. It reads Countersign's database read-only
to check what Countersign decided, and chat messages to check what Switch refused.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]

from mmlib import admin, env, persona  # noqa: E402
from post_as import demo_upload, post  # noqa: E402

from countersign import config  # noqa: E402
from countersign.tools import agentkit, rogue, scripted_intake  # noqa: E402

SETTLE_SEC = 8.0


class SetupError(RuntimeError):
    """The precondition is not met (stale database, Countersign not running, ...)."""


class Ctx:
    """Read-only DB access, Mattermost helpers and the scripted proposer."""

    def __init__(self) -> None:
        self.cfg = config.load(ROOT)
        self.db = sqlite3.connect(f"file:{self.cfg.db_path}?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self.mm = admin()
        team = self.mm.team_id(env()["MM_TEAM"])
        self.channels = {n: self.mm.channel_id(team, n) for n in ("ap-intake", "ap-approvals")}

    # ---- database
    def proposal(self, invoice: str, newest: bool = True) -> sqlite3.Row | None:
        order = "DESC" if newest else "ASC"
        return self.db.execute(
            f"SELECT * FROM proposals WHERE invoice_number=? ORDER BY opened_at_ms {order}",
            (invoice,),
        ).fetchone()

    def attempts(self, action_hash: str, user: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM approvals WHERE action_hash=? AND username=? ORDER BY id",
            (action_hash, user),
        ).fetchall()

    def ledger_total(self) -> int:
        return int(
            self.db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM ledger").fetchone()[0]
        )

    def max_seq(self) -> int:
        return int(self.db.execute("SELECT COALESCE(MAX(seq),0) FROM receipts").fetchone()[0])

    def receipts_after(self, seq: int, rtype: str) -> list[dict[str, object]]:
        rows = self.db.execute(
            "SELECT body_json FROM receipts WHERE seq>? AND type=? ORDER BY seq", (seq, rtype)
        ).fetchall()
        return [json.loads(r["body_json"]) for r in rows]

    # ---- chat
    def posts(self, channel: str, since_ms: int) -> list[tuple[str, str]]:
        r = self.mm.http.get(
            f"{self.mm.api}/channels/{self.channels[channel]}/posts", params={"per_page": 100}
        ).json()
        out = []
        for pid in reversed(r["order"]):
            p = r["posts"][pid]
            if p["create_at"] >= since_ms:
                user = self.mm.http.get(f"{self.mm.api}/users/{p['user_id']}").json()["username"]
                out.append((user, p["message"]))
        return out

    def say(self, who: str, channel: str, text: str) -> None:
        post(persona(who), channel, text)

    def approve(self, who: str, sid: str) -> None:
        self.say(who, "ap-approvals", f"@countersign approve {sid}")

    def wait(self, pred: Callable[[], bool], timeout: float = SETTLE_SEC) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.5)
        return pred()

    # ---- proposals
    def propose(self, inv_id: str, number: str) -> sqlite3.Row:
        """Upload as the fixture's persona, let scripted-intake propose, return the new proposal."""
        before = self.db.execute(
            "SELECT COUNT(*) FROM proposals WHERE invoice_number=?", (number,)
        ).fetchone()[0]
        demo_upload(inv_id, mention=False)
        time.sleep(2)
        asyncio.run(scripted_intake.run(inv_id, ROOT))
        ok = self.wait(
            lambda: (
                self.db.execute(
                    "SELECT COUNT(*) FROM proposals WHERE invoice_number=?", (number,)
                ).fetchone()[0]
                > before
            ),
            20,
        )
        row = self.proposal(number)
        if not ok or row is None:
            raise SetupError(f"no proposal appeared for {inv_id} (is `just run` running?)")
        return row


Result = tuple[bool, str]


def _code(rows: list[object], code: str) -> bool:
    return any(dict(r).get("code") == code for r in rows)  # type: ignore[call-overload]


def _clean(c: Ctx, user: str, sid_row: object, blocked_extra: str = "") -> Result:
    """No approval row exists for ``user`` and Countersign did not answer them."""
    row = dict(sid_row)  # type: ignore[call-overload]
    rows = c.attempts(row["action_hash"], user)
    return (not rows, blocked_extra or ("no approvals row" if not rows else "ROW CREATED"))


def a1(c: Ctx, st: dict[str, object]) -> Result:
    row = st["inv1042"]
    t0 = int(time.time() * 1000)
    c.approve("vikram_ceo", row["short_id"])  # type: ignore[index]
    time.sleep(SETTLE_SEC)
    refusal = [m for u, m in c.posts("ap-approvals", t0) if u not in ("vikram_ceo", "countersign")]
    ok, detail = _clean(c, "vikram_ceo", row)
    return ok, detail + (f"; Switch said: {refusal[0][:60]!r}" if refusal else "; not delivered")


def a2(c: Ctx, st: dict[str, object]) -> Result:
    row = st["inv1042"]
    answer = asyncio.run(rogue.attempt(row["short_id"], "rogue", "ap-approvals", ROOT))  # type: ignore[index]
    time.sleep(SETTLE_SEC)
    ok, detail = _clean(c, "rogue", row)
    return ok, f"{detail}; {answer}"


def a3(c: Ctx, st: dict[str, object]) -> Result:
    row, before = st["inv1042"], c.max_seq()
    asyncio.run(rogue.attempt(row["short_id"], "scripted-intake", "ap-intake", ROOT))  # type: ignore[index]
    ok = c.wait(lambda: any(
        b.get("code") == "E_WRONG_ROOM" and b.get("sender") == "scripted-intake"
        for b in c.receipts_after(before, "REFUSED")
    ), 15)  # fmt: skip
    return ok, "REFUSED receipt E_WRONG_ROOM" if ok else "no E_WRONG_ROOM receipt"


def a4(c: Ctx, st: dict[str, object]) -> Result:
    row = st["inv1043"]
    c.approve("meera", row["short_id"])  # type: ignore[index]
    ok = c.wait(lambda: _code(c.attempts(row["action_hash"], "meera"), "E_INITIATOR"))  # type: ignore[index]
    return ok, "E_INITIATOR" if ok else "no E_INITIATOR row"


def a5(c: Ctx, st: dict[str, object]) -> Result:
    row = st["inv1043"]
    c.approve("arjun", row["short_id"])  # type: ignore[index]
    time.sleep(2)
    c.approve("arjun", row["short_id"])  # type: ignore[index]
    ok = c.wait(lambda: _code(c.attempts(row["action_hash"], "arjun"), "E_DUPLICATE"))  # type: ignore[index]
    counted = sum(r["counted"] for r in c.attempts(row["action_hash"], "arjun"))  # type: ignore[index]
    return (
        ok and counted == 1,
        "E_DUPLICATE; tally 1 of 2" if ok and counted == 1 else "not blocked",
    )


def a6(c: Ctx, st: dict[str, object]) -> Result:
    row = st["inv1040"]
    c.approve("kabir", row["short_id"])  # type: ignore[index]
    ok = c.wait(lambda: _code(c.attempts(row["action_hash"], "kabir")[1:], "E_NOT_OPEN"))  # type: ignore[index]
    return ok, "E_NOT_OPEN" if ok else "no E_NOT_OPEN row"


def a7(c: Ctx, st: dict[str, object]) -> Result:
    row = c.propose("INV-1044", "INV-1044")
    time.sleep(25)
    c.approve("arjun", row["short_id"])
    ok = c.wait(lambda: _code(c.attempts(row["action_hash"], "arjun"), "E_EXPIRED"))
    return ok, "E_EXPIRED" if ok else "no E_EXPIRED row"


def a8(c: Ctx, st: dict[str, object]) -> Result:
    old, paid = st["inv1042"], c.ledger_total()
    c.approve("meera", old["short_id"])  # type: ignore[index]
    time.sleep(3)
    new = c.propose("INV-1042-R", "INV-1042")
    c.approve("arjun", old["short_id"])  # type: ignore[index]
    t0 = int(time.time() * 1000)
    c.approve("meera", new["short_id"])
    time.sleep(2)
    c.approve("kabir", new["short_id"])
    old_dead = c.wait(lambda: _code(c.attempts(old["action_hash"], "arjun"), "E_SUPERSEDED"))  # type: ignore[index]
    cb = c.wait(lambda: any(
        "E_CALLBACK_REQUIRED" in m for u, m in c.posts("ap-approvals", t0) if u == "countersign"
    ), 15)  # fmt: skip
    now = c.db.execute(
        "SELECT state FROM proposals WHERE action_hash=?", (new["action_hash"],)
    ).fetchone()
    ok = old_dead and cb and now["state"] == "OPEN" and c.ledger_total() == paid
    return ok, "old E_SUPERSEDED; new E_CALLBACK_REQUIRED; nothing paid" if ok else "not blocked"


def a9(c: Ctx, st: dict[str, object]) -> Result:
    target, before, paid = st["inv1043"], c.max_seq(), c.ledger_total()
    countersign_id = c.cfg.agent_id

    async def loosen() -> str:
        async with agentkit.connected("rogue", ROOT) as ops:
            try:
                await ops.call("update_agent_detail", agent_id=countersign_id, addressing="anyone")
                return "accepted"
            except Exception as exc:
                return f"refused ({exc})"

    outcome = asyncio.run(loosen())
    time.sleep(3)
    c.approve("meera", target["short_id"])  # type: ignore[index]
    frozen = c.wait(lambda: any(
        b.get("code") == "E_ADDRESSING_OPEN" for b in c.receipts_after(before, "FROZEN")
    ), 20)  # fmt: skip
    if outcome.startswith("refused"):
        return c.ledger_total() == paid, f"rogue's change {outcome[:70]}"
    return frozen and c.ledger_total() == paid, (
        "FROZEN E_ADDRESSING_OPEN; approval got E_FROZEN" if frozen else "NOT frozen"
    )


@dataclass(frozen=True)
class Attack:
    """One row of the red-team table."""

    id: str
    title: str
    actor: str
    layer: int
    expected: str
    run: Callable[[Ctx, dict[str, object]], Result]


ATTACKS = (
    Attack("A1", "Impersonator approves", "vikram_ceo", 1, "Switch refusal", a1),
    Attack("A2", "Agent approves in #ap-approvals", "rogue", 1, "Switch refusal", a2),
    Attack("A3", "Injected proposer approves", "scripted-intake", 2, "E_WRONG_ROOM", a3),
    Attack("A4", "Initiator self-approval", "meera", 2, "E_INITIATOR", a4),
    Attack("A5", "Duplicate approver", "arjun", 2, "E_DUPLICATE", a5),
    Attack("A6", "Replay on paid payment", "kabir", 2, "E_NOT_OPEN", a6),
    Attack("A7", "Late approval", "arjun", 2, "E_EXPIRED", a7),
    Attack("A8", "Payee swap", "riya+agents", 2, "E_SUPERSEDED, E_CALLBACK_REQ.", a8),
    Attack("A9", "Rogue loosens addressing", "rogue", 2, "E_ADDRESSING_OPEN, E_FROZEN", a9),
)  # fmt: skip


def prepare(c: Ctx) -> dict[str, object]:
    """Pay INV-1040, then open INV-1042 and INV-1043 (INV-1044 is proposed inside A7)."""
    if c.proposal("INV-1040") is not None:
        raise SetupError(
            "database is not fresh: stop Countersign, run `just reset`, start it again"
        )
    first = c.propose("INV-1040", "INV-1040")
    c.approve("kabir", first["short_id"])
    if not c.wait(lambda: (c.proposal("INV-1040") or {"state": ""})["state"] == "PAID", 30):
        raise SetupError("INV-1040 was not paid; the happy path is broken (G1)")
    paid = c.proposal("INV-1040")
    return {
        "inv1040": paid,
        "inv1042": c.propose("INV-1042", "INV-1042"),
        "inv1043": c.propose("INV-1043", "INV-1043"),
    }
