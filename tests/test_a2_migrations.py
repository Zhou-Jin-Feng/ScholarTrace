"""Schema migration: old databases stay readable, failures do not half-apply."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scholartrace.delivery.migrations import (
    CURRENT_SCHEMA_VERSION,
    SchemaMigrationError,
    migrate,
)
from scholartrace.delivery.models import DemoMode
from scholartrace.delivery.store import DeliveryStore

# The v1 shape, exactly as it existed before plan persistence was added.
V1_SCHEMA = """
CREATE TABLE tasks (
    task_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT NOT NULL,
    demo_mode TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    degradations_json TEXT NOT NULL
);
CREATE TABLE idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    request_sha256 TEXT NOT NULL,
    task_id TEXT NOT NULL REFERENCES tasks(task_id)
);
CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    artifact_type TEXT NOT NULL,
    media_type TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    content BLOB NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _make_v1(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(V1_SCHEMA)
        connection.execute(
            "INSERT INTO tasks VALUES ('task:old:1','thread:old:1','Legacy','Old question',"
            "'completed','done','success','2026-09-01T10:00:00Z','2026-09-01T10:05:00Z','{}','[]')"
        )
        connection.commit()
    finally:
        connection.close()


def test_v1_database_upgrades_and_keeps_its_tasks(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    _make_v1(path)

    store = DeliveryStore(path)
    try:
        assert store.schema_version == CURRENT_SCHEMA_VERSION
        task = store.get_task("task:old:1")
        assert task["title"] == "Legacy"
        # A pre-existing row really was a demo run, so the backfill is factual.
        assert task["execution_mode"] == "demo"
    finally:
        store.close()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    _make_v1(path)
    first = DeliveryStore(path)
    first.close()
    second = DeliveryStore(path)
    try:
        assert second.schema_version == CURRENT_SCHEMA_VERSION
        assert second.get_task("task:old:1")["task_id"] == "task:old:1"
    finally:
        second.close()


def test_future_schema_is_refused_rather_than_downgraded(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite"
    _make_v1(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_metadata VALUES ('schema_version', ?)",
            (str(CURRENT_SCHEMA_VERSION + 5),),
        )
        connection.commit()
        with pytest.raises(SchemaMigrationError, match="refusing to downgrade"):
            migrate(connection)
    finally:
        connection.close()


def test_failed_migration_leaves_the_previous_version_intact(tmp_path: Path) -> None:
    path = tmp_path / "broken.sqlite"
    _make_v1(path)
    connection = sqlite3.connect(path)
    try:
        # An occupied name makes the step fail part-way through.
        connection.execute("CREATE TABLE plans (wrong_column TEXT)")
        connection.execute("CREATE TABLE plan_approvals (approval_id TEXT PRIMARY KEY)")
        connection.commit()
        with pytest.raises(SchemaMigrationError):
            migrate(connection)
        version = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'schema_metadata'"
        ).fetchone()
        # No version was recorded, so the database is still v1 and retryable.
        assert version is None
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
        assert "execution_mode" not in columns
    finally:
        connection.close()


def test_fresh_database_lands_on_current_version(tmp_path: Path) -> None:
    store = DeliveryStore(tmp_path / "fresh.sqlite")
    try:
        assert store.schema_version == CURRENT_SCHEMA_VERSION
        tables = {
            row[0]
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"plans", "plan_approvals", "plan_cost_acknowledgements"} <= tables
        store.create_task(
            task_id="task:new:1",
            thread_id="thread:new:1",
            title="New",
            question="A new question",
            demo_mode=DemoMode.SUCCESS,
        )
        assert store.get_task("task:new:1")["execution_mode"] == "demo"
    finally:
        store.close()


def test_checkpoints_and_artifacts_stay_in_separate_files(tmp_path: Path) -> None:
    store = DeliveryStore(tmp_path / "tasks.sqlite")
    try:
        tables = {
            row[0]
            for row in store.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        # ADR-013: no LangGraph checkpoint or runtime-ledger table here.
        assert not {t for t in tables if "checkpoint" in t.lower()}
        assert "budget_effects" not in tables
        assert "events" not in tables
    finally:
        store.close()
