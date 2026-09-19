"""Guarded SQLite backup and restore for the ScholarTrace data directory."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from scholartrace import __version__
from scholartrace.restore_fence import create_restore_fence

BACKUP_SCHEMA_VERSION = "scholartrace-data-backup/1.0"
BACKUP_MANIFEST_NAME = "backup-manifest.json"
SQLITE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})
SECRET_FILENAMES = frozenset({".env", "credentials.json", "secrets.json", "token.json"})
TRANSIENT_DIRECTORY_NAMES = frozenset({"cache", "caches", "downloads", "logs", "temp", "tmp"})
RAW_DOCUMENT_SUFFIXES = frozenset({".pdf"})
MAX_MANIFEST_BYTES = 1_048_576
MAX_RESTORE_BYTES = 20 * 1024 * 1024 * 1024


class BackupError(RuntimeError):
    """The requested backup or restore operation is unsafe or invalid."""


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _normalized_relative(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise BackupError("backup path escaped the configured data directory") from exc
    return relative.as_posix()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _database_summary(connection: sqlite3.Connection) -> dict[str, int]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return {
        str(row[0]): int(
            connection.execute(f"SELECT COUNT(*) FROM {_quote_identifier(str(row[0]))}").fetchone()[
                0
            ]
        )
        for row in rows
    }


def _verify_sqlite(path: Path) -> dict[str, int]:
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
        if quick_check is None or quick_check[0] != "ok":
            raise BackupError(f"SQLite integrity check failed for {path.name}")
        return _database_summary(connection)


def _snapshot_sqlite(source: Path, destination: Path) -> dict[str, int]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = source.resolve().as_uri() + "?mode=ro"
    try:
        with (
            closing(sqlite3.connect(source_uri, uri=True, timeout=5)) as source_connection,
            closing(sqlite3.connect(destination, timeout=5)) as destination_connection,
        ):
            source_connection.backup(destination_connection)
    except sqlite3.Error as exc:
        raise BackupError(f"could not snapshot SQLite database {source.name}") from exc
    return _verify_sqlite(destination)


def _exclusion_reason(relative: Path) -> str:
    lowered_parts = {part.lower() for part in relative.parts[:-1]}
    name = relative.name.lower()
    if name in SECRET_FILENAMES or name.startswith(".env."):
        return "secret"
    if relative.suffix.lower() in RAW_DOCUMENT_SUFFIXES:
        return "raw_document"
    if lowered_parts & TRANSIENT_DIRECTORY_NAMES:
        return "cache_or_transient"
    if relative.suffix.lower() in {"-wal", "-shm"} or name.endswith(("-wal", "-shm")):
        return "sqlite_sidecar"
    return "unsupported_file"


def _inventory_data_dir(data_dir: Path) -> tuple[list[Path], dict[str, int]]:
    databases: list[Path] = []
    excluded: dict[str, int] = {
        "cache_or_transient": 0,
        "raw_document": 0,
        "secret": 0,
        "sqlite_sidecar": 0,
        "unsupported_file": 0,
    }
    for path in sorted(data_dir.rglob("*")):
        if path.is_symlink():
            excluded["unsupported_file"] += 1
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(data_dir)
        exclusion = _exclusion_reason(relative)
        if path.suffix.lower() in SQLITE_SUFFIXES and exclusion == "unsupported_file":
            databases.append(path)
        else:
            excluded[exclusion] += 1
    return databases, excluded


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def backup_data(
    *,
    data_dir: Path,
    output_path: Path,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Snapshot every SQLite database under a data root into a verified archive."""

    source_root = data_dir.resolve()
    archive_path = output_path.resolve()
    if not source_root.is_dir():
        raise BackupError("data directory does not exist or is not a directory")
    if _is_relative_to(archive_path, source_root):
        raise BackupError("backup archive must be outside the data directory")
    if archive_path.exists():
        raise BackupError("backup archive already exists")

    databases, excluded_counts = _inventory_data_dir(source_root)
    if not databases:
        raise BackupError("no SQLite databases were found under the data directory")

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = (created_at or datetime.now(UTC)).astimezone(UTC)
    manifest_entries: list[dict[str, Any]] = []
    temporary_archive = archive_path.with_name(f".{archive_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tempfile.TemporaryDirectory(prefix="scholartrace-backup-") as temporary:
            snapshot_root = Path(temporary)
            for source in databases:
                relative = _normalized_relative(source, source_root)
                snapshot = snapshot_root / relative
                tables = _snapshot_sqlite(source, snapshot)
                manifest_entries.append(
                    {
                        "archive_path": f"data/{PurePosixPath(relative)}",
                        "relative_path": relative,
                        "sha256": _sha256_file(snapshot),
                        "size_bytes": snapshot.stat().st_size,
                        "tables": tables,
                        "type": "sqlite",
                    }
                )

            manifest: dict[str, Any] = {
                "application": "ScholarTrace",
                "application_version": __version__,
                "backup_id": (
                    f"backup:{timestamp.strftime('%Y%m%dT%H%M%SZ')}:{uuid.uuid4().hex[:12]}"
                ),
                "created_at": timestamp.isoformat().replace("+00:00", "Z"),
                "exclusion_policy": {
                    "cache_or_transient": sorted(TRANSIENT_DIRECTORY_NAMES),
                    "raw_document_suffixes": sorted(RAW_DOCUMENT_SUFFIXES),
                    "secret_filenames": sorted(SECRET_FILENAMES),
                    "sqlite_sidecars": ["*-shm", "*-wal"],
                    "unsupported_files": "excluded_by_default",
                },
                "excluded_counts": excluded_counts,
                "files": manifest_entries,
                "schema_version": BACKUP_SCHEMA_VERSION,
                "source_layout": "SCHOLARTRACE_DATA_DIR",
            }
            manifest_bytes = _canonical_json(manifest)
            with zipfile.ZipFile(
                temporary_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as archive:
                archive.writestr(_zip_info(BACKUP_MANIFEST_NAME), manifest_bytes)
                for entry in manifest_entries:
                    snapshot = snapshot_root / str(entry["relative_path"])
                    archive.writestr(_zip_info(str(entry["archive_path"])), snapshot.read_bytes())
        os.replace(temporary_archive, archive_path)
    except Exception:
        temporary_archive.unlink(missing_ok=True)
        raise

    return {
        "archive_sha256": _sha256_file(archive_path),
        "backup_id": manifest["backup_id"],
        "database_count": len(manifest_entries),
        "excluded_counts": excluded_counts,
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "schema_version": BACKUP_SCHEMA_VERSION,
        "total_bytes": sum(int(entry["size_bytes"]) for entry in manifest_entries),
    }


def _safe_relative_path(raw_path: object) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise BackupError("manifest contains an invalid relative path")
    pure = PurePosixPath(raw_path)
    if pure.is_absolute() or ".." in pure.parts or pure.parts[0] in {"", "."}:
        raise BackupError("manifest contains an unsafe relative path")
    return Path(*pure.parts)


def _read_manifest(archive: zipfile.ZipFile) -> tuple[dict[str, Any], bytes]:
    try:
        info = archive.getinfo(BACKUP_MANIFEST_NAME)
    except KeyError as exc:
        raise BackupError("backup manifest is missing") from exc
    if info.file_size > MAX_MANIFEST_BYTES:
        raise BackupError("backup manifest is too large")
    manifest_bytes = archive.read(info)
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("backup manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise BackupError("backup manifest must be an object")
    if manifest.get("schema_version") != BACKUP_SCHEMA_VERSION:
        raise BackupError("unsupported backup schema version")
    if manifest.get("application") != "ScholarTrace":
        raise BackupError("backup was not created for ScholarTrace")
    if manifest.get("application_version") != __version__:
        raise BackupError("backup application version does not match this release")
    return manifest, manifest_bytes


def _validated_entries(
    archive: zipfile.ZipFile, manifest: Mapping[str, Any]
) -> list[dict[str, Any]]:
    raw_entries = manifest.get("files")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise BackupError("backup manifest has no data files")
    names = [item.filename for item in archive.infolist()]
    if len(names) != len(set(names)):
        raise BackupError("backup archive contains duplicate entries")

    entries: list[dict[str, Any]] = []
    expected_names = {BACKUP_MANIFEST_NAME}
    total_bytes = 0
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict) or raw_entry.get("type") != "sqlite":
            raise BackupError("backup manifest contains an unsupported data entry")
        relative = _safe_relative_path(raw_entry.get("relative_path"))
        if relative.suffix.lower() not in SQLITE_SUFFIXES:
            raise BackupError("backup manifest contains a non-SQLite target")
        archive_name = raw_entry.get("archive_path")
        expected_archive_name = f"data/{PurePosixPath(relative.as_posix())}"
        if archive_name != expected_archive_name:
            raise BackupError("backup manifest archive path does not match its target")
        try:
            info = archive.getinfo(expected_archive_name)
        except KeyError as exc:
            raise BackupError("backup data file is missing") from exc
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise BackupError("backup archive contains a symbolic link")
        size = raw_entry.get("size_bytes")
        if not isinstance(size, int) or size < 0 or info.file_size != size:
            raise BackupError("backup data size does not match the manifest")
        total_bytes += size
        if total_bytes > MAX_RESTORE_BYTES:
            raise BackupError("backup exceeds the restore size limit")
        sha256 = raw_entry.get("sha256")
        if not isinstance(sha256, str) or len(sha256) != 64:
            raise BackupError("backup manifest contains an invalid SHA-256")
        tables = raw_entry.get("tables")
        if not isinstance(tables, dict) or not all(
            isinstance(key, str) and isinstance(value, int) and value >= 0
            for key, value in tables.items()
        ):
            raise BackupError("backup manifest contains invalid table counts")
        expected_names.add(expected_archive_name)
        entries.append(raw_entry)
    if set(names) != expected_names:
        raise BackupError("backup archive contains files not declared by the manifest")
    return entries


def restore_data(*, archive_path: Path, target_dir: Path) -> dict[str, Any]:
    """Verify and restore a backup into a new, previously nonexistent directory."""

    source_archive = archive_path.resolve()
    destination = target_dir.resolve()
    if not source_archive.is_file():
        raise BackupError("backup archive does not exist")
    if destination.exists():
        raise BackupError("restore target must not already exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.restore-{uuid.uuid4().hex}"
    if not _is_relative_to(temporary, destination.parent):
        raise BackupError("restore staging path escaped the target parent")

    try:
        with zipfile.ZipFile(source_archive, "r") as archive:
            manifest, manifest_bytes = _read_manifest(archive)
            entries = _validated_entries(archive, manifest)
            temporary.mkdir(parents=False)
            restored_files: list[dict[str, Any]] = []
            restored_tables: dict[str, dict[str, int]] = {}
            for entry in entries:
                relative = _safe_relative_path(entry["relative_path"])
                output = temporary / relative
                if not _is_relative_to(output, temporary):
                    raise BackupError("restore file escaped the staging directory")
                output.parent.mkdir(parents=True, exist_ok=True)
                content = archive.read(str(entry["archive_path"]))
                if _sha256_bytes(content) != entry["sha256"]:
                    raise BackupError("backup data hash does not match the manifest")
                output.write_bytes(content)
                actual_tables = _verify_sqlite(output)
                if actual_tables != entry["tables"]:
                    raise BackupError("restored SQLite table counts do not match the manifest")
                restored_tables[relative.as_posix()] = actual_tables
                restored_files.append(
                    {
                        "relative_path": relative.as_posix(),
                        "sha256": _sha256_file(output),
                        "size_bytes": output.stat().st_size,
                        "tables": actual_tables,
                    }
                )
        # Every restore is potentially stale, including a backup of restored data.
        # Publish provenance with the tree; never rewrite historical SQL grants.
        create_restore_fence(temporary)
        os.replace(temporary, destination)
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        if temporary.exists():
            shutil.rmtree(temporary)
        if isinstance(exc, BackupError):
            raise
        raise BackupError("backup restore failed") from exc
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise

    return {
        "archive_sha256": _sha256_file(source_archive),
        "backup_id": manifest.get("backup_id"),
        "database_count": len(entries),
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "restored_files": restored_files,
        "restored_tables": restored_tables,
        "schema_version": BACKUP_SCHEMA_VERSION,
        "total_bytes": sum(int(entry["size_bytes"]) for entry in entries),
        "verified": True,
    }
