"""Environment variables, paths and constants, read once and validated at start (rules.md §5)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from countersign.core.models import Frozen

AGENT_NAME = "countersign"
DEFAULT_ENV = ".env.local"


class ConfigError(ValueError):
    """A required setting is missing or invalid; the message names it."""


class Config(Frozen):
    """Everything the process needs from the environment."""

    switch_url: str
    agent_id: str
    api_key: str
    db_path: Path
    mode: str
    log_level: str
    room_intake: str
    room_approvals: str
    room_audit: str
    admin_id: str | None
    root: Path


def read_env_file(path: Path) -> dict[str, str]:
    """Parse ``KEY=value`` lines (comments and blanks ignored)."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            out[key.strip()] = re.sub(r"\s+#.*$", "", value).strip().strip("'\"")
    return out


def _read_secret(path: Path, what: str) -> str:
    if not path.is_file():
        raise ConfigError(f"{what}: file not found: {path} (run `just setup`)")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ConfigError(f"{what}: file is empty: {path}")
    return value


def load(root: Path, environ: Mapping[str, str] | None = None) -> Config:
    """Build the Config from ``<root>/.env.local`` overlaid with ``environ``.

    The agent API key is read from ``COUNTERSIGN_KEY_FILE``; the agent id from
    ``COUNTERSIGN_AGENT_ID`` or the ``.id`` file next to the key. Raises ConfigError.
    """
    env = {**read_env_file(root / DEFAULT_ENV), **(environ or {})}
    switch_url = env.get("SWITCH_URL", "")
    if not switch_url.startswith("http"):
        raise ConfigError("SWITCH_URL is missing (run `just discover`)")
    key_file = Path(env.get("COUNTERSIGN_KEY_FILE", "~/.config/countersign/countersign.key"))
    key_file = key_file.expanduser()
    agent_id = env.get("COUNTERSIGN_AGENT_ID") or _read_secret(
        key_file.with_suffix(".id"), "agent id"
    )
    mode = env.get("COUNTERSIGN_MODE", "ws")
    if mode not in ("ws", "poll"):
        raise ConfigError("COUNTERSIGN_MODE must be ws or poll")
    db = Path(env.get("COUNTERSIGN_DB", "var/countersign.db")).expanduser()
    return Config(
        switch_url=switch_url.rstrip("/"),
        agent_id=agent_id,
        api_key=_read_secret(key_file, "agent key"),
        db_path=db if db.is_absolute() else root / db,
        mode=mode,
        log_level=env.get("LOG_LEVEL", "INFO").upper(),
        room_intake=env.get("ROOM_INTAKE", "ap-intake"),
        room_approvals=env.get("ROOM_APPROVALS", "ap-approvals"),
        room_audit=env.get("ROOM_AUDIT", "ap-audit"),
        admin_id=env.get("COUNTERSIGN_ADMIN_ID") or None,
        root=root,
    )
