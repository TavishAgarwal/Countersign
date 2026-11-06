"""Minimal shared client for the tool agents (scripted-intake, rogue). Reuses switch/ adapters.

These agents are untrusted by design: they only post messages. Each opens a short-lived
connection (ops need one), does its job and closes.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from countersign.config import ConfigError, read_env_file
from countersign.switch.models import ConnectionState
from countersign.switch.ops import OpsClient, make_client
from countersign.switch.ws import WsTransport

CONFIG_DIR = Path("~/.config/countersign").expanduser()
CONNECT_TIMEOUT_SEC = 15.0


def load_identity(name: str, root: Path) -> tuple[str, str, str]:
    """Return (switch_url, agent_id, api_key) for a registered agent, from .env.local and files."""
    env = read_env_file(root / ".env.local")
    url = env.get("SWITCH_URL", "")
    if not url.startswith("http"):
        raise ConfigError("SWITCH_URL is missing (run `just discover`)")
    var = f"{name.upper().replace('-', '_')}_KEY_FILE"
    key_file = Path(env.get(var, str(CONFIG_DIR / f"{name}.key"))).expanduser()
    id_file = key_file.with_suffix(".id")
    for f in (key_file, id_file):
        if not f.is_file():
            raise ConfigError(f"{f} not found (run `just setup` to register agents)")
    return url.rstrip("/"), id_file.read_text().strip(), key_file.read_text().strip()


@contextlib.asynccontextmanager
async def connected(name: str, root: Path) -> AsyncIterator[OpsClient]:
    """Open a connection as agent ``name`` and yield an OpsClient bound to it."""
    url, agent_id, key = load_identity(name, root)
    conn_id = str(uuid.uuid4())
    transport = WsTransport(url, agent_id, key, conn_id, rooms=[])
    ready = asyncio.Event()

    async def pump() -> None:
        async for frame in transport.events():
            if isinstance(frame, ConnectionState):
                ready.set()

    task = asyncio.create_task(pump())
    try:
        await asyncio.wait_for(ready.wait(), CONNECT_TIMEOUT_SEC)
        async with make_client() as client:
            yield OpsClient(url, agent_id, key, lambda: conn_id, client)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def room_id(ops: OpsClient, name: str) -> str:
    """Find a room by name or raise ConfigError."""
    found = [r.room_id for r in await ops.list_all_rooms() if r.name == name]
    if not found:
        raise ConfigError(f"room {name!r} not visible to this agent")
    if len(found) > 1:
        raise ConfigError(
            f"{len(found)} rooms are named {name!r}; delete the duplicates in Console"
        )
    return found[0]
