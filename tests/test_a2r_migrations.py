"""Migration failures preserve the complete prior schema and data."""

import sqlite3
from pathlib import Path

import pytest
from test_a2_migrations import _make_v1

from scholartrace.delivery import migrations


@pytest.mark.parametrize("version", [2, 3, 4])
def test_failed_step_rolls_back_all_schema_and_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: int
) -> None:
    path = tmp_path / "old.sqlite"
    _make_v1(path)
    with sqlite3.connect(path) as connection:
        before = list(connection.iterdump())
        original = migrations.MIGRATIONS[version]

        def fail_after_ddl(conn: sqlite3.Connection) -> None:
            original(conn)
            raise sqlite3.OperationalError("injected after DDL")

        monkeypatch.setitem(migrations.MIGRATIONS, version, fail_after_ddl)
        with pytest.raises(migrations.SchemaMigrationError):
            migrations.migrate(connection)
        assert list(connection.iterdump()) == before
        monkeypatch.setitem(migrations.MIGRATIONS, version, original)
        assert migrations.migrate(connection) == migrations.CURRENT_SCHEMA_VERSION
        assert connection.execute("SELECT title FROM tasks").fetchone() == ("Legacy",)


def test_nested_migration_does_not_commit_the_callers_transaction(tmp_path):
    path = tmp_path / "nested.sqlite"
    _make_v1(path)
    with sqlite3.connect(path) as connection:
        before = list(connection.iterdump())
        connection.execute("BEGIN")
        connection.execute("UPDATE tasks SET title = 'Uncommitted'")
        migrations.migrate(connection)
        connection.rollback()
        assert list(connection.iterdump()) == before


def test_v3_settled_usage_survives_upgrade_and_failed_upgrade(tmp_path, monkeypatch):
    path = tmp_path / "v3.sqlite"
    _make_v1(path)
    with sqlite3.connect(path) as connection:
        with monkeypatch.context() as context:
            context.setattr(migrations, "CURRENT_SCHEMA_VERSION", 3)
            migrations.migrate(connection)
        connection.execute(
            "INSERT INTO budget_reservations (task_id,plan_version,plan_digest,reserved_cny,"
            "reserved_api_calls,reserved_wall_clock_seconds,estimate_source,reserved_at,"
            "settled_at,settled_cny,settled_api_calls) "
            "VALUES ('task:old:1',1,'digest',10,4,60,'fixture','then','now',2.5,1)"
        )
        connection.commit()
        before = list(connection.iterdump())
        original = migrations.MIGRATIONS[4]

        def failed(conn):
            original(conn)
            raise RuntimeError("injected failure after new columns")

        with monkeypatch.context() as context:
            context.setitem(migrations.MIGRATIONS, 4, failed)
            with pytest.raises(migrations.SchemaMigrationError):
                migrations.migrate(connection)
        assert list(connection.iterdump()) == before
        migrations.migrate(connection)
        assert connection.execute(
            "SELECT settled_cny,recorded_cny,recorded_api_calls FROM budget_reservations"
        ).fetchone() == (2.5, 2.5, 1)
