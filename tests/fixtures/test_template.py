"""The Console template is current and has the shape the design needs."""

from __future__ import annotations

import subprocess
import sys

import yaml

from tests.conftest import ROOT


def load() -> dict[str, dict[str, object]]:
    return yaml.safe_load((ROOT / "switch" / "countersign.template.yaml").read_text())


def test_template_is_up_to_date() -> None:
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_template.py"), "--check"])
    assert run.returncode == 0


def test_template_shape() -> None:
    t = load()
    res = t["resources"]
    assert t["version"] == 2
    assert {r["name"] for r in res.values() if r["type"] == "room"} == {
        "ap-intake", "ap-approvals", "ap-audit",
    }  # fmt: skip
    intake = res["intake_agent"]
    assert intake["provider"] == "claude" and intake["auto_approve"] is False
    assert intake["can_manage_agents"] is False
    assert "Never" in str(intake["instructions"]) and "approve" in str(intake["instructions"])
    assert res["approvals"]["agents"] == ["countersign", "rogue"]
    assert "intake_agent" not in res["approvals"]["agents"]


def test_workspace_vendor_list_matches_vendor_file() -> None:
    text = (ROOT / "agents" / "intake" / "workspace" / "CLAUDE.md").read_text()
    vendors = yaml.safe_load((ROOT / "switch" / "vendors.yaml").read_text())["vendors"]
    for v in vendors:
        assert v["vendor_id"] in text and v["account_number"] in text
