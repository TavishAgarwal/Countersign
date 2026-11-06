"""Run the nine attacks and print the table (design.md §10.1). Exit 0 only if all are blocked.

just redteam                 # restores Countersign's addressing at the end (asks for the password)
uv run python redteam/run.py --no-restore
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from redteam.attacks import ATTACKS, Ctx, SetupError, prepare  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-restore", action="store_true", help="leave Countersign FROZEN after A9")
    args = ap.parse_args()
    out = Console()
    try:
        ctx = Ctx()
        state = prepare(ctx)
    except SetupError as exc:
        out.print(f"[red]setup failed:[/red] {exc}")
        return 2
    paid_before = ctx.ledger_total()
    ist = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M IST")
    table = Table(title=f"Countersign red team · {ist} · {len(ATTACKS)} attacks")
    for col in ("#", "Attack", "Actor", "Layer", "Expected", "Result", "Detail"):
        table.add_column(col)
    blocked = 0
    for atk in ATTACKS:
        try:
            ok, detail = atk.run(ctx, state)
        except Exception as exc:  # a crashed attack is a failed check, never a pass
            ok, detail = False, f"error: {type(exc).__name__}: {exc}"[:90]
        blocked += ok
        mark = "[green]✅ blocked[/green]" if ok else "[red]❌ NOT BLOCKED[/red]"
        table.add_row(atk.id, atk.title, atk.actor, str(atk.layer), atk.expected, mark, detail)
    out.print(table)
    unchanged = ctx.ledger_total() == paid_before
    out.print(
        f"{blocked}/{len(ATTACKS)} blocked · ledger {'unchanged' if unchanged else 'CHANGED'} "
        f"since INV-1040"
    )
    if not args.no_restore:
        out.print("Restoring Countersign's addressing rules (Gateway admin password needed)…")
        subprocess.run([sys.executable, str(ROOT / "scripts" / "set_addressing.py")], check=False)
    return 0 if blocked == len(ATTACKS) and unchanged else 1


if __name__ == "__main__":
    sys.exit(main())
