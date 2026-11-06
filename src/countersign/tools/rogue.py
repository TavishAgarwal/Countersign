"""Red-team agent: tries to approve a payment from a room it should not be able to use.

``python -m countersign.tools.rogue CS-xxxxxx [--agent rogue] [--room ap-approvals]``. Layer 1
(Switch addressing) should refuse it; if a message ever gets through, Countersign's identity
check (layer 2) must still say no. The outcome is printed for the red-team runner.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from countersign.config import ConfigError
from countersign.switch.ops import SwitchError
from countersign.tools import agentkit

ROOT = Path(__file__).resolve().parents[3]


async def attempt(short_id: str, agent: str, room_name: str, root: Path = ROOT) -> str:
    """Post ``@countersign approve <id>`` as ``agent``; returns what Switch answered."""
    async with agentkit.connected(agent, root) as ops:
        room = await agentkit.room_id(ops, room_name)
        await ops.connect_to_room(room)
        try:
            event_id = await ops.post_message(f"@countersign approve {short_id}")
        except SwitchError as exc:
            return f"refused by Switch ({exc.status})"
        return f"posted (event {event_id})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rogue")
    ap.add_argument("short_id")
    ap.add_argument("--agent", default="rogue")
    ap.add_argument("--room", default="ap-approvals")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        print(asyncio.run(attempt(args.short_id, args.agent, args.room)))
    except (ConfigError, SwitchError, TimeoutError) as exc:
        print(f"rogue failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
