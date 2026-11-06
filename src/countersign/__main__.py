"""CLI entry: run | verify | bank | status | accept-policy."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from countersign import config
from countersign.chat import render
from countersign.store import db, ledger, receipts, repo

USAGE = "usage: countersign run | verify | bank | status | accept-policy"


def _cfg() -> config.Config:
    return config.load(Path.cwd())


def cmd_verify() -> int:
    """Re-check the receipt chain against the ledger. Exit 0 only when intact (design §10.2)."""
    conn = db.open_db(_cfg().db_path)
    st = receipts.verify(conn)
    if st.ok:
        print(f"✅ chain OK · {st.count} receipts · head {render.short_hash(st.head)}")
        return 0
    print(f"❌ chain BROKEN at receipt #{st.first_bad_seq} ({st.reason})", file=sys.stderr)
    return 1


def cmd_bank() -> int:
    """Print the mock ledger (design §10.3)."""
    conn = db.open_db(_cfg().db_path)
    table = Table()
    for col in ("#", "time (UTC)", "vendor", "account", "amount", "via", "payment id"):
        table.add_column(col)
    for e in ledger.entries(conn):
        table.add_row(
            str(e.id), render.iso_utc(e.ts_ms), e.vendor_id, f"****{e.account_last4}",
            render.usd(e.amount_cents), e.via, e.action_hash[:9],
        )  # fmt: skip
    Console().print(table)
    print(f"total paid {render.usd(ledger.total_cents(conn))}")
    return 0


def cmd_status() -> int:
    """Print every proposal, and whether payments are frozen (design §10.4)."""
    conn = db.open_db(_cfg().db_path)
    state = conn.execute(
        "SELECT type, body_json FROM receipts WHERE type IN ('FROZEN','UNFROZEN') "
        "ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    if state is not None and state["type"] == "FROZEN":
        print(f"🧊 FROZEN · {json.loads(state['body_json']).get('code', '?')}")
    else:
        print("payments ACTIVE")
    for r in repo.list_all(conn):
        what = r.invoice_number or "autopilot"
        print(f"{r.short_id}  {r.state.value:<9} {r.vendor_id:<12} "
              f"{render.usd(r.amount_cents or 0):>14}  {what}")  # fmt: skip
    for rule in repo.rules(conn):
        print(f"{rule.rule_id}  {rule.state.value:<9} {rule.vendor_id:<12} "
              f"up to {render.usd(rule.max_cents)} · {render.usd(rule.spent_cents)} of "
              f"{render.usd(rule.cap_cents)} used")  # fmt: skip
    return 0


async def _accept_policy() -> int:
    from countersign.accept import accept_policy
    from countersign.runtime import Runtime
    from countersign.switch.poster import Poster
    from countersign.tools import agentkit

    cfg = _cfg()
    async with agentkit.connected("countersign", cfg.root) as ops:
        poster = Poster(ops)
        worker = asyncio.create_task(poster.run())
        try:
            conn = db.open_db(cfg.db_path)
            rt = Runtime(cfg, conn, ops, poster)
            return await accept_policy(rt, lambda q: input(q).strip().lower() == "y")
        finally:
            worker.cancel()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    cmd = args[0] if args else "help"
    try:
        if cmd == "run":
            cfg = _cfg()
            logging.basicConfig(
                level=cfg.log_level.upper(), format="%(asctime)s %(name)s %(message)s"
            )
            from countersign.app import run

            return asyncio.run(run(cfg))
        if cmd == "verify":
            return cmd_verify()
        if cmd == "bank":
            return cmd_bank()
        if cmd == "status":
            return cmd_status()
    except config.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    if cmd == "accept-policy":
        return asyncio.run(_accept_policy())
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
