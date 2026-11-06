"""Set Countersign's layer-1 addressing rules R1-R3 (architecture.md §11.1) through the Gateway API.

Replaces the "click Custom rules in the Gateway UI" step: Console-run local servers have no Gateway
UI, but the Gateway API is served at <SWITCH_URL>/gateway (Swagger at /gateway/docs).

  uv run python scripts/set_addressing.py --dry-run   # resolve ids, print the body, change nothing
  uv run python scripts/set_addressing.py             # log in to the Gateway and apply

Ids are resolved at run time: approver ids come from list_participants of #ap-approvals (type user,
names from switch/policy.yaml); intake agent ids from list_agents (names from policy proposers).
Login: the Gateway admin account of the local server. The email and password are read from
GATEWAY_ADMIN_EMAIL / GATEWAY_ADMIN_PASSWORD in .env.local if set, else the password is prompted.
R3 names the Switch admin (id from GET /gateway/auth/me after login, saved as COUNTERSIGN_ADMIN_ID
in .env.local). Run it again after adding an approver or registering an intake agent.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx
import websockets
import yaml

ROOT = Path(__file__).resolve().parent.parent
ADMIN_PLACEHOLDER = "<switch admin id, resolved at login>"
CFG = Path.home() / ".config" / "countersign"
ENV = dict(
    line.split("=", 1) for line in (ROOT / ".env.local").read_text().splitlines() if "=" in line
)


async def resolve() -> tuple[str, dict[str, object]]:
    """Return (countersign agent id, policy body) resolved from the live server."""
    base = ENV["SWITCH_URL"]
    aid, key = (CFG / "countersign.id").read_text(), (CFG / "countersign.key").read_text()
    cid = str(uuid.uuid4())
    policy = yaml.safe_load((ROOT / "switch" / "policy.yaml").read_text())
    names = {
        ENV.get("ROOM_INTAKE", "ap-intake"),
        ENV.get("ROOM_APPROVALS", "ap-approvals"),
        ENV.get("ROOM_AUDIT", "ap-audit"),
    }
    ws_base = base.replace("http", "ws", 1)
    url = f"{ws_base}/agents/{aid}/connection/ws?connection_id={cid}&scope=all&filter=addressed"
    hdr = {"Authorization": f"Bearer {key}", "X-Switch-Connection-Id": cid}
    async with websockets.connect(
        url, additional_headers={"Authorization": f"Bearer {key}"}, ping_interval=None
    ) as ws:

        async def pump() -> None:
            async for raw in ws:
                f = json.loads(raw)
                if f.get("type") == "ping" or f.get("event") == "ping":
                    await ws.send(json.dumps({"type": "pong", "cursor": 0}))

        task = asyncio.create_task(pump())
        async with httpx.AsyncClient(timeout=20) as c:

            async def op(name: str, **args: object) -> Any:
                r = await c.post(f"{base}/agents/{aid}/ops/{name}", json=args, headers=hdr)
                r.raise_for_status()
                return r.json()["result"]

            listed = await op("list_all_rooms")
            for n in names:
                if sum(r["name"] == n for r in listed) > 1:
                    sys.exit(f"more than one room is named {n}; delete the duplicates in Console")
            rooms = {r["name"]: r["room_id"] if "room_id" in r else r["id"] for r in listed}
            missing = names - rooms.keys()
            if missing:
                sys.exit(f"rooms not found: {sorted(missing)} (run the template or create them)")
            await op("connect_to_room", room_id=rooms[ENV.get("ROOM_APPROVALS", "ap-approvals")])
            people = await op("list_participants")
            approvers = set(policy["approvers"])
            users = [p["id"] for p in people if p["type"] == "user" and p["name"] in approvers]
            absent = set(policy["approvers"]) - {p["name"] for p in people}
            if absent:
                sys.exit(
                    f"approvers not in #ap-approvals: {sorted(absent)} (run just setup-members)"
                )
            agents = [a["id"] for a in await op("list_agents") if a["name"] in policy["proposers"]]
        task.cancel()
    approvals, intake = rooms["ap-approvals"], rooms["ap-intake"]
    rules: list[dict[str, object]] = [
        {
            "rooms": [approvals],
            "users": users,
            "agents": [],
            "owner": False,
            "owner_agents": False,
            "platform": False,
        },
        {
            "rooms": list(rooms[n] for n in sorted(names)),
            "users": [ADMIN_PLACEHOLDER],
            "agents": [],
            "owner": False,
            "owner_agents": False,
            "platform": False,
        },
    ]
    if agents:
        rules.insert(
            1,
            {
                "rooms": [intake],
                "users": [],
                "agents": agents,
                "owner": False,
                "owner_agents": False,
                "platform": False,
            },
        )
    return aid, {"policy": {"rules": rules}}


def save_admin_id(admin_id: str) -> None:
    """Write COUNTERSIGN_ADMIN_ID into .env.local (Countersign's addressing check allows it)."""
    path = ROOT / ".env.local"
    lines = [
        ln for ln in path.read_text().splitlines() if not ln.startswith("COUNTERSIGN_ADMIN_ID=")
    ]
    path.write_text("\n".join([*lines, f"COUNTERSIGN_ADMIN_ID={admin_id}"]) + "\n")


def apply(aid: str, body: dict[str, object]) -> None:
    base = ENV["SWITCH_URL"]
    email = ENV.get("GATEWAY_ADMIN_EMAIL") or input("Gateway admin email: ")
    password = ENV.get("GATEWAY_ADMIN_PASSWORD") or getpass.getpass("Gateway admin password: ")
    with httpx.Client(timeout=20) as c:
        r = c.post(f"{base}/gateway/auth/login", json={"email": email, "password": password})
        if r.status_code != 200:
            sys.exit(f"Gateway login failed: {r.status_code}")
        me = c.get(f"{base}/gateway/auth/me")
        if me.status_code != 200 or not me.json().get("id"):
            sys.exit(f"could not read the Gateway admin id: {me.status_code}")
        admin_id = me.json()["id"]
        for rule in body["policy"]["rules"]:
            rule["users"] = [admin_id if u == ADMIN_PLACEHOLDER else u for u in rule["users"]]
        save_admin_id(admin_id)
        r = c.put(f"{base}/gateway/agents/{aid}/addressing-policy", json=body)
        if r.status_code != 200:
            sys.exit(f"addressing-policy update failed: {r.status_code} {r.text[:300]}")
    print("addressing policy applied:", len(body["policy"]["rules"]), "rules")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    aid, body = asyncio.run(resolve())
    print(json.dumps(body, indent=2))
    if not args.dry_run:
        apply(aid, body)


if __name__ == "__main__":
    main()
