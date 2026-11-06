"""Open the database: WAL, foreign keys, schema version, explicit transactions (CS-F11)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1
_SCHEMA = Path(__file__).with_name("schema.sql")


class SchemaError(RuntimeError):
    """The database was created by a newer or unknown schema version."""


def open_db(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the database at ``path`` and migrate it to SCHEMA_VERSION.

    Autocommit is off for statements outside ``transaction()`` only in the sense that every
    write the app makes goes through ``transaction()``; the connection itself uses
    ``isolation_level=None`` so transactions are explicit. File mode is 600 for new files.
    Raises SchemaError on an unknown future version.
    """
    target = str(path)
    is_new = target == ":memory:" or not Path(target).exists()
    if target != ":memory:":
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, isolation_level=None)
    conn.row_factory = sqlite3.Row
    if target != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
        if is_new:
            Path(target).chmod(0o600)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=FULL")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    row = conn.execute("PRAGMA user_version").fetchone()
    version = int(row[0])
    if version > SCHEMA_VERSION:
        raise SchemaError(f"database schema {version} is newer than supported {SCHEMA_VERSION}")
    if version < SCHEMA_VERSION:
        conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """``BEGIN IMMEDIATE`` ... commit, or roll back on any exception (then re-raise)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
