"""Purity guard (rules.md §5, P1-T12): fail if core/ imports I/O, clock or randomness,
or if any model SDK is imported anywhere under src/countersign/ except tools/ (rules §1.2).

Usage: python scripts/check_purity.py [core_dir] [src_dir]
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

FORBIDDEN_IN_CORE = {"httpx", "websockets", "sqlite3", "os", "time", "random", "logging"}
FORBIDDEN_LLM = {"anthropic", "openai", "claude_agent_sdk", "langchain", "google.generativeai"}


def _imports(path: Path) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            found += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.lineno, node.module))
    return found


def _hits(name: str, banned: set[str]) -> bool:
    return any(name == b or name.startswith(b + ".") for b in banned)


def check(core_dir: Path, src_dir: Path) -> list[str]:
    """Return one message per violation."""
    problems: list[str] = []
    for path in sorted(core_dir.rglob("*.py")):
        for line, name in _imports(path):
            if _hits(name, FORBIDDEN_IN_CORE):
                problems.append(f"{path}:{line}: core/ must not import {name}")
    for path in sorted(src_dir.rglob("*.py")):
        if "tools" in path.relative_to(src_dir).parts:
            continue
        for line, name in _imports(path):
            if _hits(name, FORBIDDEN_LLM):
                problems.append(f"{path}:{line}: no LLM SDK outside tools/ ({name})")
    return problems


def main() -> int:
    core = Path(sys.argv[1] if len(sys.argv) > 1 else "src/countersign/core")
    src = Path(sys.argv[2] if len(sys.argv) > 2 else "src/countersign")
    problems = check(core, src)
    for p in problems:
        print(p, file=sys.stderr)
    if not problems:
        print("purity ok")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
