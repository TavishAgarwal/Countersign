"""P3-T10: upload the 9 demo invoices for the Claude `intake` agent and check what it proposes.

For each invoice the uploader persona posts the PDF with "@intake please process ...", then this
script waits for `intake`'s `@countersign propose` message and compares it with the fixture:
valid schema, same vendor, number, dates, amount and bank details, and intake_flags matching the
expected flags (architecture.md §8.2). Exit code 0 only when all 9 are right.

  uv run python scripts/check_intake.py [--timeout-min 30]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import yaml
from mmlib import ROOT, admin, env
from post_as import demo_upload, post  # noqa: F401

sys.path.insert(0, str(ROOT / "src"))
from countersign.core.commands import extract_json_block
from countersign.core.models import Proposal
from countersign.tools.scripted_intake import build_proposal

SKIP = {"INV-1042-R"}
FIELDS = (
    "vendor_id", "invoice_number", "invoice_date", "due_date", "amount_cents", "currency", "bank",
)  # fmt: skip


def intake_proposals(mm, channel_id: str, uid: str, since_ms: int) -> list[dict[str, Any]]:
    posts = mm.http.get(f"{mm.api}/channels/{channel_id}/posts", params={"per_page": 200}).json()
    out = []
    for pid in posts["order"]:
        p = posts["posts"][pid]
        if (
            p["user_id"] != uid
            or p["create_at"] < since_ms
            or "@countersign propose" not in p["message"]
        ):
            continue
        block = extract_json_block(p["message"])
        if block:
            try:
                out.append(json.loads(block))
            except json.JSONDecodeError:
                continue
    return out


def judge(inv: dict[str, Any], got: dict[str, Any] | None) -> list[str]:
    """Return the list of problems (empty when the proposal is right)."""
    if got is None:
        return ["no proposal"]
    problems: list[str] = []
    try:
        Proposal.model_validate(got)
    except Exception as exc:
        return [f"invalid schema: {str(exc)[:80]}"]
    want = build_proposal(inv, "x", "y")
    problems += [f"{k}: {got.get(k)!r} != {want[k]!r}" for k in FIELDS if got.get(k) != want[k]]
    flags = sorted(f["code"] for f in got.get("intake_flags", []))
    expected = sorted(inv["expect"].get("flags", []))
    if flags != expected:
        problems.append(f"flags {flags} != {expected}")
    return problems


def countersign_verdict(db: sqlite3.Connection | None, inv: dict[str, Any]) -> str | None:
    """Did Countersign accept the proposal (or refuse it for the expected reason)? None = yes."""
    if db is None:
        return None
    expect = inv["expect"]
    if "refused" in expect:
        rows = db.execute("SELECT body_json FROM receipts WHERE type = 'REFUSED'").fetchall()
        hit = any(json.loads(r[0]).get("code") == expect["refused"] for r in rows)
        return None if hit else f"Countersign did not refuse it with {expect['refused']}"
    row = db.execute(
        "SELECT state FROM proposals WHERE invoice_number = ?", (inv["number"],)
    ).fetchone()
    if row is not None:
        return None
    codes = [json.loads(r[0]).get("code") for r in db.execute(
        "SELECT body_json FROM receipts WHERE type = 'REFUSED'")]  # fmt: skip
    return f"Countersign refused or ignored it (refusals: {codes})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout-min", type=float, default=30)
    ap.add_argument("--db", type=Path, default=ROOT / "var" / "countersign.db",
                    help="Countersign's fresh database, with Countersign running")  # fmt: skip
    args = ap.parse_args()
    invoices = [
        i
        for i in yaml.safe_load((ROOT / "fixtures" / "invoices.yaml").read_text())["invoices"]
        if i["id"] not in SKIP
    ]
    mm = admin()
    channel = mm.channel_id(mm.team_id(env()["MM_TEAM"]), "ap-intake")
    uid = mm.user_id_of("intake") or ""
    since = int(time.time() * 1000)
    for inv in invoices:
        demo_upload(inv["id"])
        time.sleep(1)
    deadline = time.time() + args.timeout_min * 60
    found: dict[str, dict[str, Any]] = {}
    while time.time() < deadline and len(found) < len(invoices):
        time.sleep(15)
        for prop in intake_proposals(mm, channel, uid, since):
            name = str(prop.get("attachment", {}).get("filename", "")).removesuffix(".pdf")
            found.setdefault(name, prop)
        print(f"... {len(found)}/{len(invoices)} proposals seen", flush=True)
    db = None
    if str(args.db) and args.db.is_file():
        time.sleep(20)  # let Countersign finish the last proposals
        db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    ok = 0
    for inv in invoices:
        problems = judge(inv, found.get(inv["id"]))
        verdict = countersign_verdict(db, inv)
        if verdict:
            problems.append(verdict)
        ok += not problems
        print(f"{inv['id']}: {'OK' if not problems else '; '.join(problems)}")
    print(
        f"{ok}/{len(invoices)} valid proposals from the Claude intake agent, "
        "accepted by Countersign"
    )
    return 0 if ok == len(invoices) else 1


if __name__ == "__main__":
    sys.exit(main())
