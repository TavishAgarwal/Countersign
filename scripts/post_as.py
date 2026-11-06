"""Post a message or upload a PDF as a persona (demo and red team).

post_as.py --as kabir --channel ap-approvals "@countersign approve CS-7f3a2c"
post_as.py --as riya --channel ap-intake --file fixtures/invoices/INV-1040.pdf "@intake go"
post_as.py --demo-upload INV-1040      # uploader and text come from fixtures/invoices.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from mmlib import ROOT, Mattermost, env, persona


def post(mm: Mattermost, channel: str, text: str, file: Path | None = None) -> str:
    team = mm.team_id(env()["MM_TEAM"])
    cid = mm.channel_id(team, channel)
    body: dict[str, object] = {"channel_id": cid, "message": text}
    if file is not None:
        r = mm.http.post(
            f"{mm.api}/files", params={"channel_id": cid},
            files={"files": (file.name, file.read_bytes(), "application/pdf")},
        )  # fmt: skip
        r.raise_for_status()
        body["file_ids"] = [r.json()["file_infos"][0]["id"]]
    r = mm.post("/posts", body)
    if r.status_code not in (200, 201):
        raise SystemExit(f"post failed ({r.status_code}): {r.text[:200]}")
    return str(r.json()["id"])


def demo_upload(inv_id: str, mention: bool = True) -> None:
    data = yaml.safe_load((ROOT / "fixtures" / "invoices.yaml").read_text())["invoices"]
    inv = next((i for i in data if i["id"] == inv_id), None)
    if inv is None:
        raise SystemExit(f"{inv_id} is not in fixtures/invoices.yaml")
    pdf = ROOT / "fixtures" / "invoices" / f"{inv_id}.pdf"
    if not pdf.is_file():
        raise SystemExit(f"{pdf} missing (run `uv run python scripts/seed_invoices.py`)")
    text = f"@intake please process {inv_id}" if mention else f"Invoice {inv_id} attached"
    pid = post(persona(inv["uploader"]), "ap-intake", text, pdf)
    print(f"{inv['uploader']} uploaded {pdf.name} (post {pid})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as", dest="who")
    ap.add_argument("--channel")
    ap.add_argument("--file", type=Path)
    ap.add_argument("--demo-upload", metavar="INV")
    ap.add_argument("--no-mention", action="store_true", help="do not wake the Claude intake agent")
    ap.add_argument("text", nargs="?", default="")
    a = ap.parse_args()
    if a.demo_upload:
        demo_upload(a.demo_upload, mention=not a.no_mention)
        return 0
    if not (a.who and a.channel and (a.text or a.file)):
        ap.error("need --as, --channel and text or --file (or --demo-upload INV)")
    print("posted", post(persona(a.who), a.channel, a.text, a.file))
    return 0


if __name__ == "__main__":
    sys.exit(main())
