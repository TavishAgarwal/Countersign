"""Small Mattermost REST client for the setup scripts, post_as and the red team."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PERSONAS_FILE = Path(
    os.environ.get("PERSONA_PASSWORD_FILE", "~/.config/countersign/personas.json")
).expanduser()

# username -> (first, last, position)
PERSONAS: dict[str, tuple[str, str, str]] = {
    "riya": ("Riya", "Shah", "AP Clerk"),
    "meera": ("Meera", "Iyer", "Controller"),
    "arjun": ("Arjun", "Mehta", "CFO"),
    "kabir": ("Kabir", "Khan", "Finance Manager"),
    "vikram_ceo": ("Vikram", "Rao", "CEO"),
}
CHANNEL_MEMBERS: dict[str, tuple[str, ...]] = {
    "ap-intake": ("riya", "meera", "arjun", "kabir"),
    "ap-approvals": ("meera", "arjun", "kabir", "riya", "vikram_ceo"),
    "ap-audit": ("riya", "meera", "arjun", "kabir", "vikram_ceo"),
}


def env() -> dict[str, str]:
    from countersign.config import read_env_file

    return {**read_env_file(ROOT / ".env.local"), **os.environ}


def load_personas() -> dict[str, str]:
    return json.loads(PERSONAS_FILE.read_text()) if PERSONAS_FILE.is_file() else {}


def save_personas(data: dict[str, str]) -> None:
    PERSONAS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PERSONAS_FILE.write_text(json.dumps(data, indent=2))
    PERSONAS_FILE.chmod(0o600)


class Mattermost:
    """A logged-in Mattermost session."""

    def __init__(self, base: str, login_id: str, password: str) -> None:
        self.api = base.rstrip("/") + "/api/v4"
        self.http = httpx.Client(timeout=20)
        r = self.http.post(
            f"{self.api}/users/login", json={"login_id": login_id, "password": password}
        )
        if r.status_code != 200:
            raise RuntimeError(f"Mattermost login failed for {login_id} ({r.status_code})")
        self.http.headers["Authorization"] = "Bearer " + r.headers["Token"]
        self.user_id: str = r.json()["id"]

    def get(self, path: str) -> dict[str, object]:
        r = self.http.get(self.api + path)
        r.raise_for_status()
        return dict(r.json())

    def post(self, path: str, body: dict[str, object]) -> httpx.Response:
        return self.http.post(self.api + path, json=body)

    def team_id(self, name: str) -> str:
        return str(self.get(f"/teams/name/{name}")["id"])

    def channel_id(self, team_id: str, name: str) -> str:
        """Find an open channel by display name (the template may add a suffix to its URL name)."""
        r = self.http.get(f"{self.api}/teams/{team_id}/channels", params={"per_page": 200})
        r.raise_for_status()
        found = [c["id"] for c in r.json() if c["display_name"] == name]
        if len(found) != 1:
            raise RuntimeError(
                f"{len(found)} Mattermost channels are named {name!r} (need exactly 1)"
            )
        return str(found[0])

    def user_id_of(self, username: str) -> str | None:
        r = self.http.get(f"{self.api}/users/username/{username}")
        return str(r.json()["id"]) if r.status_code == 200 else None


def admin() -> Mattermost:
    e = env()
    for key in ("MM_URL", "MM_ADMIN_USER", "MM_ADMIN_PASSWORD", "MM_TEAM"):
        if not e.get(key):
            raise SystemExit(f"{key} is empty in .env.local (README: Mattermost admin login)")
    return Mattermost(e["MM_URL"], e["MM_ADMIN_USER"], e["MM_ADMIN_PASSWORD"])


def persona(name: str) -> Mattermost:
    passwords = load_personas()
    if name not in passwords:
        raise SystemExit(f"no password for {name} (run `just setup`)")
    return Mattermost(env()["MM_URL"], name, passwords[name])
