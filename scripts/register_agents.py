"""Register countersign, scripted-intake and rogue with the bootstrap key.

Keys go to ~/.config/countersign/.

Existing agents are skipped (a key is shown once, so re-registering would lose it). Keys and ids
are written with mode 600 and never printed. The Claude `intake` agent comes from the template.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CFG = Path.home() / ".config" / "countersign"
AGENTS = {
    "countersign": "Countersign: pays invoices only when enough listed humans approve",
    "scripted-intake": "Scripted intake: deterministic proposer for the demo (no LLM)",
    "rogue": "Rogue: red-team agent that tries to approve payments",
}


def main() -> int:
    from countersign.config import read_env_file

    env = read_env_file(ROOT / ".env.local")
    url, reg = env.get("SWITCH_URL", ""), env.get("SWITCH_REGISTRATION_KEY", "")
    if not url or not reg:
        print("SWITCH_URL / SWITCH_REGISTRATION_KEY missing: run `just discover`", file=sys.stderr)
        return 1
    CFG.mkdir(parents=True, exist_ok=True)
    for name, desc in AGENTS.items():
        key = CFG / f"{name}.key"
        if key.is_file():
            print(f"{name}: already registered, skipped")
            continue
        body = {
            "name": name, "description": desc, "display_name": name, "connector_type": "custom",
            "integration_profile": {
                "connection_model": "always_on", "message_exchange": True,
                "pre_invocation_mediation": [], "post_invocation_mediation": [],
                "event_reporting": [],
            },
        }  # fmt: skip
        r = httpx.post(f"{url}/agents", json=body, timeout=15,
                       headers={"Authorization": f"Bearer {reg}"})  # fmt: skip
        if r.status_code not in (200, 201):
            print(f"{name}: registration failed ({r.status_code})", file=sys.stderr)
            return 1
        data = r.json()
        key.write_text(data["api_key"])
        key.chmod(0o600)
        key.with_suffix(".id").write_text(data["id"])
        key.with_suffix(".id").chmod(0o600)
        print(f"{name}: registered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
