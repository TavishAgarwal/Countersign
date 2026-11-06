"""Find the local Switch server and write .env.local (architecture.md §5.1, §12; PRD CS-F16).

Reads, from Switch Console's data folder, the server ports (ports.json) and the bootstrap
registration token (native/run/switch-core.env), checks the server answers, and writes
SWITCH_URL, MM_URL and SWITCH_REGISTRATION_KEY into .env.local (chmod 600). Other values already in
.env.local are kept; a missing file is created from .env.example. The token is never printed.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env.local"
CANDIDATES = [
    Path.home() / "Library/Application Support/switchdash/local-switch-server",  # macOS
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "switchdash/local-switch-server",
]
HELP = (
    "Could not find Switch Console's local server files, or the server is not running.\n"
    "Open Switch Console, Add a server > Run a server on this computer > Start, then run again.\n"
    "To fill .env.local by hand instead, follow the README section 'Fill .env.local by hand'."
)


def fail(message: str) -> None:
    sys.exit(f"discover: {message}\n{HELP}")


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            out[key.strip()] = re.sub(r"\s+#.*$", "", value).strip().strip("'\"")
    return out


def find_server_dir() -> Path:
    for base in CANDIDATES:
        if (base / "ports.json").is_file():
            return base
    fail("ports.json not found")
    raise SystemExit(1)


def main() -> None:
    base = find_server_dir()
    try:
        ports = json.loads((base / "ports.json").read_text())
        api_port, mm_port = int(ports["api"]), int(ports["mattermost"])
    except (ValueError, KeyError, OSError) as exc:
        fail(f"cannot read ports.json ({type(exc).__name__})")
    server_env = base / "native/run/switch-core.env"
    token = read_env(server_env).get("AGENT_REGISTRATION_TOKEN", "") if server_env.is_file() else ""
    if not token:
        fail("AGENT_REGISTRATION_TOKEN not found in switch-core.env")
    api = f"http://127.0.0.1:{api_port}"
    try:
        ok = httpx.get(f"{api}/health", timeout=5).status_code == 200
    except httpx.HTTPError:
        ok = False
    if not ok:
        fail(f"the Switch server does not answer at {api}/health")

    source = ENV_FILE if ENV_FILE.exists() else ROOT / ".env.example"
    values = read_env(source)
    values.update(
        SWITCH_URL=api, MM_URL=f"http://127.0.0.1:{mm_port}", SWITCH_REGISTRATION_KEY=token
    )
    ENV_FILE.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    ENV_FILE.chmod(0o600)
    print(
        f"wrote {ENV_FILE.name}: SWITCH_URL={api} MM_URL=http://127.0.0.1:{mm_port} (token saved)"
    )
    missing = [k for k in ("MM_ADMIN_USER", "MM_ADMIN_PASSWORD", "MM_TEAM") if not values.get(k)]
    if missing:
        print(
            f"still to fill in {ENV_FILE.name}: {', '.join(missing)} "
            "(Switch Console > Home > Mattermost > Sign-in details...)"
        )


if __name__ == "__main__":
    main()
