"""Create the policy.yaml and vendors.yaml room documents in #ap-approvals (once).

Templates cannot express room documents (spike S7), so Countersign's own agent identity creates
them with `create_room_document`. Existing documents are left alone, so this is safe to re-run.
Edits made afterwards freeze payments until ratified (architecture.md §5.7).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from countersign.switch.ops import SwitchError
from countersign.tools import agentkit

ROOT = Path(__file__).resolve().parent.parent
DOCS = {"policy.yaml": "Approval policy", "vendors.yaml": "Vendor file"}


async def main() -> int:
    async with agentkit.connected("countersign", ROOT) as ops:
        room = await agentkit.room_id(ops, "ap-approvals")
        info = await ops.connect_to_room(room)
        have = {d.name for d in info.documents}
        for name, description in DOCS.items():
            if name in have:
                print(f"{name}: exists, left alone")
                continue
            await ops.call(
                "create_room_document", name=name, description=description,
                instructions="Editing this document freezes payments until ratified.",
                content=(ROOT / "switch" / name).read_text(encoding="utf-8"),
            )  # fmt: skip
            print(f"{name}: created")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except (SwitchError, TimeoutError, RuntimeError) as exc:
        print(f"create_docs failed: {exc}", file=sys.stderr)
        sys.exit(1)
