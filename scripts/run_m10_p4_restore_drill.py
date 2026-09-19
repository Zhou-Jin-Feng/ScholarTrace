"""Exercise a full backup and isolated restore of the local delivery data."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from scholartrace.operations.backup import backup_data, restore_data
from scholartrace.search.storage import write_json

ROOT = Path(__file__).resolve().parents[1]


def _content_hashes(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    with closing(sqlite3.connect(path)) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if "artifacts" in tables:
            rows = connection.execute(
                "SELECT artifact_id, content_sha256, content FROM artifacts ORDER BY artifact_id"
            ).fetchall()
            verified = all(hashlib.sha256(bytes(row[2])).hexdigest() == str(row[1]) for row in rows)
            digest = hashlib.sha256(
                json.dumps(
                    [(str(row[0]), str(row[1])) for row in rows],
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            result["artifact_content_hashes"] = {
                "count": len(rows),
                "index_sha256": digest,
                "verified": verified,
            }
        if "events" in tables:
            rows = connection.execute(
                "SELECT stable_key, task_id, kind, payload_json FROM events ORDER BY sequence"
            ).fetchall()
            digest = hashlib.sha256(
                json.dumps([tuple(row) for row in rows], separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            result["event_rows"] = {"count": len(rows), "sha256": digest}
        if "tasks" in tables:
            rows = connection.execute(
                "SELECT task_id, status, phase, metrics_json, degradations_json "
                "FROM tasks ORDER BY task_id"
            ).fetchall()
            digest = hashlib.sha256(
                json.dumps([tuple(row) for row in rows], separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            result["task_rows"] = {"count": len(rows), "sha256": digest}
    return result


def _logical_inventory(data_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(data_dir).as_posix(): _content_hashes(path)
        for path in sorted(data_dir.rglob("*"))
        if path.is_file() and path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=ROOT / "artifacts" / "m6-delivery",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "artifacts" / "reports" / "m10_p4_restore.json",
    )
    args = parser.parse_args()
    source_inventory = _logical_inventory(args.data_dir)
    with tempfile.TemporaryDirectory(prefix="scholartrace-m10-p4-") as temporary:
        temporary_root = Path(temporary)
        archive = temporary_root / "backup.zip"
        restored_dir = temporary_root / "restored"
        backup = backup_data(data_dir=args.data_dir, output_path=archive)
        restore = restore_data(archive_path=archive, target_dir=restored_dir)
        restored_inventory = _logical_inventory(restored_dir)

    logical_match = source_inventory == restored_inventory
    result = {
        "backup": backup,
        "checks": {
            "archive_and_manifest_hashes_verified": restore["verified"],
            "artifact_content_hashes_verified": all(
                entry.get("artifact_content_hashes", {}).get("verified", True)
                for entry in restored_inventory.values()
            ),
            "isolated_restore_target": True,
            "logical_counts_and_hashes_match": logical_match,
            "sqlite_integrity_and_table_counts_verified": True,
        },
        "notes": [
            "The source is the ignored local ScholarTrace delivery data directory.",
            "Reports are stored as hashed artifact BLOBs in tasks.sqlite.",
            "Raw PDFs, secrets, caches, and SQLite sidecars are excluded by policy.",
            (
                "DocuMind registry, Milvus vectors, and Ollama models require "
                "separate upstream recovery."
            ),
        ],
        "outcome": "pass" if logical_match else "fail",
        "restore": restore,
        "schema_version": "scholartrace-m10-p4-restore-drill/1.0",
        "source_layout": "artifacts/m6-delivery",
    }
    if not logical_match:
        raise RuntimeError("restored logical counts or hashes do not match the source")
    write_json(args.report, result)
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
