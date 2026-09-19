"""Versioned schema migrations for the delivery store.

Design constraints from T04 §7 / T23:

* migrations are transactional — a failed migration leaves the database at its
  previous version rather than half-applied;
* pre-existing tasks stay readable: rows created before the research-plan tables
  existed are backfilled with ``execution_mode='demo'`` and no plan version,
  which is what they actually were;
* LangGraph checkpoints and business artifacts remain in separate files
  (ADR-013); nothing here touches them.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

#: Schema version this build expects. Bump together with a new migration step.
CURRENT_SCHEMA_VERSION = 4

MigrationStep = Callable[[sqlite3.Connection], None]


class SchemaMigrationError(RuntimeError):
    """A migration could not be applied; the database is unchanged."""


def _read_version(connection: sqlite3.Connection) -> int:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    row = connection.execute(
        "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
    ).fetchone()
    if row is not None:
        return int(row[0])
    # A database that already has `tasks` but no recorded version is a v1
    # database created before migrations existed.
    existing = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
    ).fetchone()
    return 1 if existing is not None else 0


def _write_version(connection: sqlite3.Connection, version: int) -> None:
    connection.execute(
        "INSERT INTO schema_metadata (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(version),),
    )


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    """Add research-plan persistence, approval records and execution mode."""

    statements = """
        CREATE TABLE IF NOT EXISTS plans (
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            plan_version INTEGER NOT NULL,
            plan_digest TEXT NOT NULL,
            generated_by TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            approval_state TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            supersedes_version INTEGER,
            PRIMARY KEY (task_id, plan_version)
        );
        CREATE TABLE IF NOT EXISTS plan_approvals (
            approval_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            plan_version INTEGER NOT NULL,
            plan_digest TEXT NOT NULL,
            action TEXT NOT NULL,
            reason TEXT,
            decided_at TEXT NOT NULL,
            idempotency_key TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS plan_approvals_idem
            ON plan_approvals (idempotency_key)
            WHERE idempotency_key IS NOT NULL;
        CREATE TABLE IF NOT EXISTS plan_cost_acknowledgements (
            task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
            acknowledged_max_cny REAL NOT NULL,
            estimate_source TEXT NOT NULL,
            acknowledged_at TEXT NOT NULL
        );
        """
    for statement in statements.split(";"):
        if statement.strip():
            connection.execute(statement)
    # Stable list ordering for the task list cursor (created_at, task_id).
    connection.execute(
        "CREATE INDEX IF NOT EXISTS tasks_list_order ON tasks (created_at DESC, task_id DESC)"
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
    if "execution_mode" not in columns:
        # Existing rows really were demo runs, so the backfill is a statement of
        # fact rather than an assumption.
        connection.execute(
            "ALTER TABLE tasks ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'demo'"
        )


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    """Add whole-task budget reservations (T07).

    Deliberately a separate table from the ledger's `budget_effects`: effects
    record what was actually consumed, reservations record what was set aside.
    Mixing them would make a reservation indistinguishable from a real charge,
    which is exactly the overstatement this project must not make.

    A reservation carries its own terminal settlement columns so a crash between
    reserve and settle is detectable: an unsettled reservation on a terminal task
    is a reconcilable fact, not a silent leak.
    """

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS budget_reservations (
            task_id TEXT PRIMARY KEY,
            plan_version INTEGER NOT NULL,
            plan_digest TEXT NOT NULL,
            reserved_cny REAL NOT NULL,
            reserved_api_calls INTEGER NOT NULL,
            reserved_wall_clock_seconds INTEGER NOT NULL,
            estimate_source TEXT NOT NULL,
            reserved_at TEXT NOT NULL,
            settled_at TEXT,
            settled_cny REAL,
            settled_api_calls INTEGER,
            settled_reason TEXT,
            -- False unless reconciled against a real provider bill. A settlement
            -- computed from local counters is an accounting record, not a bill.
            is_actual_bill INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    # Finding unsettled reservations after a restart must not scan every row.
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS budget_reservations_open
        ON budget_reservations (settled_at)
        WHERE settled_at IS NULL
        """
    )


def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
    """Preserve known usage separately from unconfirmed external exposure."""
    for column in (
        "reconciliation_required INTEGER NOT NULL DEFAULT 0",
        "recorded_cny REAL NOT NULL DEFAULT 0",
        "recorded_api_calls INTEGER NOT NULL DEFAULT 0",
    ):
        connection.execute(f"ALTER TABLE budget_reservations ADD COLUMN {column}")
    connection.execute(
        "UPDATE budget_reservations SET recorded_cny = COALESCE(settled_cny, 0), "
        "recorded_api_calls = COALESCE(settled_api_calls, 0) WHERE settled_at IS NOT NULL"
    )


MIGRATIONS: dict[int, MigrationStep] = {
    2: _migrate_v1_to_v2,
    3: _migrate_v2_to_v3,
    4: _migrate_v3_to_v4,
}


def migrate(connection: sqlite3.Connection) -> int:
    """Bring ``connection`` up to :data:`CURRENT_SCHEMA_VERSION`.

    Returns the resulting version. Raises :class:`SchemaMigrationError` without
    committing anything if a step fails or the database is from the future.
    """

    # SAVEPOINT also works inside a caller-owned transaction and never commits it.
    # No migration step may use executescript: sqlite3 implicitly commits first.
    connection.execute("SAVEPOINT delivery_migration")
    try:
        current = _read_version(connection)
        if current > CURRENT_SCHEMA_VERSION:
            raise SchemaMigrationError(
                f"database schema v{current} is newer than supported "
                f"v{CURRENT_SCHEMA_VERSION}; refusing to downgrade"
            )
        for version in range(max(current, 1) + 1, CURRENT_SCHEMA_VERSION + 1):
            step = MIGRATIONS.get(version)
            if step is None:
                raise SchemaMigrationError(f"missing migration step for v{version}")
            step(connection)
        _write_version(connection, CURRENT_SCHEMA_VERSION)
        connection.execute("RELEASE SAVEPOINT delivery_migration")
    except Exception as exc:
        connection.execute("ROLLBACK TO SAVEPOINT delivery_migration")
        connection.execute("RELEASE SAVEPOINT delivery_migration")
        if isinstance(exc, SchemaMigrationError):
            raise
        raise SchemaMigrationError("migration failed and was rolled back") from exc
    return CURRENT_SCHEMA_VERSION
