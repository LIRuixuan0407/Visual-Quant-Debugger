from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 4


def ensure_schema_version(connection: sqlite3.Connection) -> int:
    """Apply the shared, explicit SQLite metadata migration chain."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    row = connection.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        return SCHEMA_VERSION
    version = int(row["value"])
    if version == 1:
        # Phase 10 adds component-owned paper_sessions tables. The migration marker is
        # shared; each repository creates its own tables idempotently below this call.
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'schema_version'",
            ("2",),
        )
        version = 2
    if version == 2:
        # Phase 11 component repositories add framework runtime metadata columns
        # idempotently after advancing the shared migration marker.
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'schema_version'",
            ("3",),
        )
        version = 3
    if version == 3:
        # Phase 12 adds persistent paper accounts, orders, fills, and PAPER runs.
        # Component repositories create their tables and columns idempotently.
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'schema_version'",
            (str(SCHEMA_VERSION),),
        )
        return SCHEMA_VERSION
    if version != SCHEMA_VERSION:
        raise RuntimeError(f"Unsupported VQD schema version {version}; expected {SCHEMA_VERSION}")
    return version
