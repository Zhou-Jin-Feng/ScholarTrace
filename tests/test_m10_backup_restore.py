from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scholartrace.delivery.models import DemoMode
from scholartrace.delivery.store import DeliveryStore
from scholartrace.operations.backup import BackupError, backup_data, restore_data
from scholartrace.operations.cli import backup_main, restore_main
from scholartrace.workflow.storage import RuntimeLedger


def _seed_live_data(data_dir: Path) -> tuple[DeliveryStore, RuntimeLedger]:
    store = DeliveryStore(data_dir / "tasks.sqlite")
    ledger = RuntimeLedger(data_dir / "runtime.sqlite")
    store.create_task(
        task_id="task:m10:p4",
        thread_id="thread:m10:p4",
        title="Backup drill",
        question="Can this task be restored?",
        demo_mode=DemoMode.SUCCESS,
    )
    store.add_artifact(
        task_id="task:m10:p4",
        artifact_id="artifact:m10:p4:report:markdown",
        artifact_type="report",
        media_type="text/markdown; charset=utf-8",
        content=b"# Verified report\n",
    )
    ledger.append_event(
        stable_key="event:m10:p4:created",
        task_id="task:m10:p4",
        node="test",
        kind="task_created",
    )
    return store, ledger


def test_online_sqlite_backup_restores_tasks_events_and_reports(tmp_path: Path) -> None:
    data_dir = tmp_path / "live"
    store, ledger = _seed_live_data(data_dir)
    archive = tmp_path / "backup.zip"
    target = tmp_path / "restored"
    try:
        backup = backup_data(
            data_dir=data_dir,
            output_path=archive,
            created_at=datetime(2026, 9, 4, tzinfo=UTC),
        )
    finally:
        ledger.close()
        store.close()

    restored = restore_data(archive_path=archive, target_dir=target)
    assert backup["database_count"] == 2
    assert restored["verified"] is True
    assert restored["restored_tables"]["tasks.sqlite"]["tasks"] == 1
    assert restored["restored_tables"]["tasks.sqlite"]["artifacts"] == 1
    assert restored["restored_tables"]["runtime.sqlite"]["events"] == 1

    restored_store = DeliveryStore(target / "tasks.sqlite")
    restored_ledger = RuntimeLedger(target / "runtime.sqlite")
    try:
        artifact = restored_store.get_artifact("artifact:m10:p4:report:markdown")
        assert artifact["content"] == b"# Verified report\n"
        assert restored_ledger.event_count("task:m10:p4") == 1
    finally:
        restored_ledger.close()
        restored_store.close()


def test_backup_excludes_secrets_raw_documents_and_transient_files(tmp_path: Path) -> None:
    data_dir = tmp_path / "live"
    store, ledger = _seed_live_data(data_dir)
    (data_dir / ".env").write_text("TOKEN=do-not-copy", encoding="utf-8")
    (data_dir / "paper.pdf").write_bytes(b"%PDF-private")
    cache = data_dir / "cache"
    cache.mkdir()
    (cache / "response.json").write_text("secret response", encoding="utf-8")
    archive = tmp_path / "backup.zip"
    try:
        result = backup_data(data_dir=data_dir, output_path=archive)
    finally:
        ledger.close()
        store.close()

    assert result["excluded_counts"]["secret"] == 1
    assert result["excluded_counts"]["raw_document"] == 1
    assert result["excluded_counts"]["cache_or_transient"] == 1
    with zipfile.ZipFile(archive) as handle:
        combined = b"\n".join(handle.read(name) for name in handle.namelist())
        assert b"do-not-copy" not in combined
        assert b"%PDF-private" not in combined
        assert b"secret response" not in combined


def test_restore_rejects_tampering_and_existing_target(tmp_path: Path) -> None:
    data_dir = tmp_path / "live"
    store, ledger = _seed_live_data(data_dir)
    archive = tmp_path / "backup.zip"
    try:
        backup_data(data_dir=data_dir, output_path=archive)
    finally:
        ledger.close()
        store.close()

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(BackupError, match="must not already exist"):
        restore_data(archive_path=archive, target_dir=existing)

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "data/tasks.sqlite":
                content += b"tampered"
            target.writestr(info, content)
    restore_target = tmp_path / "tampered-restore"
    with pytest.raises(BackupError, match="size does not match"):
        restore_data(archive_path=tampered, target_dir=restore_target)
    assert not restore_target.exists()


def test_backup_recursively_captures_workflow_sqlite_stores(tmp_path: Path) -> None:
    data_dir = tmp_path / "live"
    workflow_dir = data_dir / "workflow"
    workflow_dir.mkdir(parents=True)
    with sqlite3.connect(workflow_dir / "checkpoints.sqlite") as connection:
        connection.execute("CREATE TABLE checkpoints(thread_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO checkpoints VALUES ('thread:one')")
    archive = tmp_path / "backup.zip"
    backup_data(data_dir=data_dir, output_path=archive)
    result = restore_data(archive_path=archive, target_dir=tmp_path / "restored")
    assert result["restored_tables"]["workflow/checkpoints.sqlite"] == {"checkpoints": 1}


def test_backup_excludes_sqlite_files_inside_cache_directories(tmp_path: Path) -> None:
    data_dir = tmp_path / "live"
    cache = data_dir / "cache"
    cache.mkdir(parents=True)
    with sqlite3.connect(data_dir / "tasks.sqlite") as connection:
        connection.execute("CREATE TABLE tasks(task_id TEXT PRIMARY KEY)")
    with sqlite3.connect(cache / "responses.sqlite") as connection:
        connection.execute("CREATE TABLE responses(content TEXT)")
    archive = tmp_path / "backup.zip"
    result = backup_data(data_dir=data_dir, output_path=archive)
    assert result["database_count"] == 1
    assert result["excluded_counts"]["cache_or_transient"] == 1
    with zipfile.ZipFile(archive) as handle:
        assert "data/cache/responses.sqlite" not in handle.namelist()


def test_manifest_is_canonical_and_does_not_expose_source_path(tmp_path: Path) -> None:
    data_dir = tmp_path / "private-user-name" / "live"
    store, ledger = _seed_live_data(data_dir)
    archive = tmp_path / "backup.zip"
    try:
        backup_data(data_dir=data_dir, output_path=archive)
    finally:
        ledger.close()
        store.close()
    with zipfile.ZipFile(archive) as handle:
        raw_manifest = handle.read("backup-manifest.json")
    manifest = json.loads(raw_manifest)
    assert manifest["source_layout"] == "SCHOLARTRACE_DATA_DIR"
    assert b"private-user-name" not in raw_manifest


def test_installed_cli_entry_points_run_backup_and_restore(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "live"
    store, ledger = _seed_live_data(data_dir)
    archive = tmp_path / "backup.zip"
    try:
        backup_main(["--data-dir", str(data_dir), "--output", str(archive)])
    finally:
        ledger.close()
        store.close()
    backup_output = json.loads(capsys.readouterr().out)
    assert backup_output["database_count"] == 2

    target = tmp_path / "restored"
    restore_main(["--archive", str(archive), "--target-dir", str(target)])
    restore_output = json.loads(capsys.readouterr().out)
    assert restore_output["verified"] is True


@pytest.mark.parametrize(
    ("manifest_change", "message"),
    [
        ({"application_version": "0.4.0"}, "version does not match"),
        ({"application": "AnotherProduct"}, "not created for ScholarTrace"),
    ],
)
def test_restore_rejects_incompatible_manifest_identity(
    tmp_path: Path, manifest_change: dict[str, str], message: str
) -> None:
    data_dir = tmp_path / "live"
    store, ledger = _seed_live_data(data_dir)
    source_archive = tmp_path / "backup.zip"
    try:
        backup_data(data_dir=data_dir, output_path=source_archive)
    finally:
        ledger.close()
        store.close()

    changed_archive = tmp_path / "changed.zip"
    with zipfile.ZipFile(source_archive) as source, zipfile.ZipFile(changed_archive, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "backup-manifest.json":
                manifest = json.loads(content)
                manifest.update(manifest_change)
                content = json.dumps(manifest).encode("utf-8")
            target.writestr(info, content)

    with pytest.raises(BackupError, match=message):
        restore_data(archive_path=changed_archive, target_dir=tmp_path / "restored")


def test_restore_rejects_manifest_path_traversal(tmp_path: Path) -> None:
    data_dir = tmp_path / "live"
    store, ledger = _seed_live_data(data_dir)
    source_archive = tmp_path / "backup.zip"
    try:
        backup_data(data_dir=data_dir, output_path=source_archive)
    finally:
        ledger.close()
        store.close()

    changed_archive = tmp_path / "changed.zip"
    with zipfile.ZipFile(source_archive) as source, zipfile.ZipFile(changed_archive, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename == "backup-manifest.json":
                manifest = json.loads(content)
                manifest["files"][0]["relative_path"] = "../escape.sqlite"
                content = json.dumps(manifest).encode("utf-8")
            target.writestr(info, content)

    with pytest.raises(BackupError, match="unsafe relative path"):
        restore_data(archive_path=changed_archive, target_dir=tmp_path / "restored")
    assert not (tmp_path / "escape.sqlite").exists()
