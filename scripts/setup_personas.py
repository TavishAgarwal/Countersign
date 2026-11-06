"""Create the five Mattermost test users, add them to the team (and with --members, the channels).

Passwords are generated once and kept in PERSONA_PASSWORD_FILE (mode 600); they are never printed.
Existing users are left alone. Channel membership needs the template run first
(`just setup-members`).
"""

from __future__ import annotations

import argparse
import secrets
import sys

from mmlib import CHANNEL_MEMBERS, PERSONAS, Mattermost, admin, load_personas, save_personas


def create_users(mm: Mattermost, team_id: str) -> None:
    passwords = load_personas()
    for name, (first, last, position) in PERSONAS.items():
        uid = mm.user_id_of(name)
        if uid is None:
            passwords.setdefault(name, secrets.token_urlsafe(12) + "Aa1!")
            r = mm.post("/users", {
                "email": f"{name}@example.com", "username": name, "password": passwords[name],
                "first_name": first, "last_name": last, "nickname": f"{first} {last}",
                "position": position,
            })  # fmt: skip
            if r.status_code not in (200, 201):
                raise SystemExit(f"could not create {name} ({r.status_code})")
            uid = str(r.json()["id"])
            print(f"created {name}")
        elif name not in passwords:
            print(f"{name} exists but has no saved password; reset it and add it to the file")
        mm.post(f"/teams/{team_id}/members", {"team_id": team_id, "user_id": uid})
    save_personas(passwords)


def add_members(mm: Mattermost, team_id: str) -> int:
    missing = 0
    for channel, names in CHANNEL_MEMBERS.items():
        try:
            cid = mm.channel_id(team_id, channel)
        except Exception:
            print(f"channel {channel} not found (run the Console template first)")
            missing += 1
            continue
        for name in names:
            uid = mm.user_id_of(name)
            if uid is None:
                print(f"user {name} missing (run `just setup`)")
                missing += 1
                continue
            mm.post(f"/channels/{cid}/members", {"user_id": uid})
        print(f"#{channel}: {', '.join(names)}")
    return missing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--members", action="store_true", help="add personas to the three channels")
    args = ap.parse_args()
    mm = admin()
    from mmlib import env

    team_id = mm.team_id(env()["MM_TEAM"])
    if args.members:
        return 1 if add_members(mm, team_id) else 0
    create_users(mm, team_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
