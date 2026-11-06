"""Take real Mattermost screenshots with the locally installed Google Chrome (no downloads).

  uv run python scripts/screenshots.py [--as meera] [--out docs/media] [--theme light]

Logs in over the Mattermost API as a test persona, hands the session cookie to a headless Chrome
and saves one PNG per channel (intake, approvals, audit). Needs the demo data to exist already.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets
from mmlib import ROOT, env, load_personas

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9333
CHANNELS = ("ap-intake", "ap-approvals", "ap-audit")


def login(who: str) -> tuple[str, str]:
    """Return (session token, user id) for a persona."""
    base = env()["MM_URL"].rstrip("/") + "/api/v4"
    r = httpx.post(
        f"{base}/users/login", json={"login_id": who, "password": load_personas()[who]}, timeout=15
    )
    r.raise_for_status()
    return r.headers["Token"], r.json()["id"]


def channel_slugs(who: str, token: str) -> dict[str, str]:
    """Map display names to the URL names (the template may add a suffix)."""
    e = env()
    base = e["MM_URL"].rstrip("/") + "/api/v4"
    h = {"Authorization": f"Bearer {token}"}
    team = httpx.get(f"{base}/teams/name/{e['MM_TEAM']}", headers=h, timeout=15).json()["id"]
    chans = httpx.get(
        f"{base}/teams/{team}/channels", headers=h, params={"per_page": 200}, timeout=15
    )
    out = {c["display_name"]: c["name"] for c in chans.json() if c["display_name"] in CHANNELS}
    missing = [c for c in CHANNELS if c not in out]
    if missing:
        raise SystemExit(f"{who} cannot see {missing}")
    return out


async def cdp(ws, counter: list[int], method: str, **params: object) -> dict:
    counter[0] += 1
    await ws.send(json.dumps({"id": counter[0], "method": method, "params": params}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == counter[0]:
            return msg.get("result", {})


async def shoot(who: str, out: Path, theme: str) -> None:
    token, uid = login(who)
    slugs = channel_slugs(who, token)
    base, team = env()["MM_URL"].rstrip("/"), env()["MM_TEAM"]
    host = base.split("//")[1].split(":")[0]
    profile = tempfile.mkdtemp(prefix="cs-chrome-")
    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
         "--window-size=1500,950", "--hide-scrollbars", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        for _ in range(40):
            try:
                tabs = httpx.get(f"http://127.0.0.1:{PORT}/json", timeout=2).json()
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            raise SystemExit("Chrome did not start")
        page = next(t for t in tabs if t["type"] == "page")
        async with websockets.connect(page["webSocketDebuggerUrl"], max_size=2**26) as ws:
            n = [0]
            await cdp(ws, n, "Network.enable")
            for name, value in (("MMAUTHTOKEN", token), ("MMUSERID", uid)):
                await cdp(ws, n, "Network.setCookie", name=name, value=value, domain=host, path="/")
            await cdp(ws, n, "Emulation.setDeviceMetricsOverride", width=1500, height=950,
                      deviceScaleFactor=1, mobile=False)  # fmt: skip
            await cdp(ws, n, "Emulation.setEmulatedMedia",
                      features=[{"name": "prefers-color-scheme", "value": theme}])  # fmt: skip
            await cdp(ws, n, "Page.navigate", url=f"{base}/login")
            await asyncio.sleep(3)
            for key, value in (
                ("__landingPageSeen__", "true"),
                ("onboarding_task_list_open", "false"),
            ):
                await cdp(ws, n, "Runtime.evaluate",
                          expression=f"localStorage.setItem({key!r}, {value!r})")  # fmt: skip
            for display, slug in slugs.items():
                await cdp(ws, n, "Page.navigate", url=f"{base}/{team}/channels/{slug}")
                await asyncio.sleep(6)
                text = (await cdp(ws, n, "Runtime.evaluate", expression="document.body.innerText",
                                  returnByValue=True))["result"].get("value", "")  # fmt: skip
                if "Log in" in text[:400] or display not in text:
                    raise SystemExit(
                        f"{display}: the page did not show the channel: {text[:300]!r}"
                    )
                shot = await cdp(ws, n, "Page.captureScreenshot", format="png")
                import base64

                path = out / f"{display}.png"
                path.write_bytes(base64.b64decode(shot["data"]))
                print("saved", path)
    finally:
        proc.terminate()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as", dest="who", default="meera")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "media")
    ap.add_argument("--theme", default="light", choices=("light", "dark"))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    asyncio.run(shoot(a.who, a.out, a.theme))
    return 0


if __name__ == "__main__":
    sys.exit(main())
