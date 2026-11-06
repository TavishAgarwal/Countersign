from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.conftest import ROOT

SCRIPT = ROOT / "scripts" / "check_purity.py"


def run(core: Path, src: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(core), str(src)], capture_output=True, text=True
    )


def test_real_core_is_pure() -> None:
    assert run(ROOT / "src/countersign/core", ROOT / "src/countersign").returncode == 0


def test_planted_forbidden_import_fails(tmp_path: Path) -> None:
    core = tmp_path / "core"
    core.mkdir()
    for bad in (
        "import time",
        "import os.path",
        "from sqlite3 import connect",
        "import httpx",
        "import logging",
    ):
        (core / "x.py").write_text(bad + "\n")
        res = run(core, tmp_path)
        assert res.returncode == 1 and "core/ must not import" in res.stderr, bad


def test_planted_llm_import_outside_tools_fails(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "app.py").write_text("import anthropic\n")
    assert run(tmp_path / "core", tmp_path).returncode == 1
    (tmp_path / "app.py").unlink()
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "t.py").write_text("import anthropic\n")
    assert run(tmp_path / "core", tmp_path).returncode == 0
