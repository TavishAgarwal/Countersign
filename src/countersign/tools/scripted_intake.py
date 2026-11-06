"""Deterministic stand-in for the Claude intake agent (CS-F19). No LLM.

``python -m countersign.tools.scripted_intake INV-1042`` finds the uploaded PDF in #ap-intake and
posts ``@countersign propose`` + the JSON block built from fixtures/invoices.yaml. Like the real
intake agent it is untrusted: Countersign re-derives everything it acts on.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from countersign.config import ConfigError
from countersign.switch.ops import OpsClient, SwitchError
from countersign.tools import agentkit

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures" / "invoices.yaml"
INTAKE_ROOM = "ap-intake"


def load_fixture(inv_id: str, path: Path = FIXTURES) -> dict[str, Any]:
    """Return one invoice entry from the fixture file."""
    for inv in yaml.safe_load(path.read_text(encoding="utf-8"))["invoices"]:
        if inv["id"] == inv_id:
            return dict(inv)
    raise ConfigError(f"{inv_id} is not in {path.name}")


def build_proposal(inv: dict[str, Any], source_message_id: str, mxc: str) -> dict[str, Any]:
    """The proposal object for a fixture invoice (architecture.md §10)."""
    return {
        "type": "payment_proposal",
        "schema": 1,
        "source_message_id": source_message_id,
        "attachment": {"filename": f"{inv['id']}.pdf", "mxc": mxc},
        "vendor_id": inv["vendor_id"],
        "invoice_number": inv["number"],
        "invoice_date": inv["invoice_date"],
        "due_date": inv["due_date"],
        "amount_cents": inv["amount_cents"],
        "currency": "USD",
        "bank": {"account_number": inv["account_number"], "routing_number": inv["routing_number"]},
        "intake_flags": [],
        "intake_note": f"Scripted intake: fixture {inv['id']}",
    }


def render_body(proposal: dict[str, Any]) -> str:
    """The chat message: the mention, then exactly one fenced json block."""
    return "@countersign propose\n```json\n" + json.dumps(proposal, indent=2) + "\n```"


async def find_upload(ops: OpsClient, room: str, filename: str) -> tuple[str, str]:
    """Newest message in the room carrying ``filename``: (message id, mxc)."""
    for msg in reversed(await ops.read_context(room, limit=100)):
        atts = msg.get("attachments") or []
        if isinstance(atts, list):
            for a in atts:
                if isinstance(a, dict) and a.get("filename") == filename and a.get("mxc"):
                    return str(msg["id"]), str(a["mxc"])
    raise ConfigError(f"no upload named {filename} in #{INTAKE_ROOM} (run `just demo-upload`)")


async def run(inv_id: str, root: Path = ROOT) -> str:
    """Post the proposal for ``inv_id``; returns the posted event id."""
    inv = load_fixture(inv_id)
    async with agentkit.connected("scripted-intake", root) as ops:
        room = await agentkit.room_id(ops, INTAKE_ROOM)
        await ops.connect_to_room(room)
        msg_id, mxc = await find_upload(ops, room, f"{inv_id}.pdf")
        return await ops.post_message(render_body(build_proposal(inv, msg_id, mxc)))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m countersign.tools.scripted_intake INV-1042", file=sys.stderr)
        return 2
    try:
        event_id = asyncio.run(run(args[0]))
    except (ConfigError, SwitchError, TimeoutError) as exc:
        print(f"scripted-intake failed: {exc}", file=sys.stderr)
        return 1
    print(f"posted proposal for {args[0]} (event {event_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
