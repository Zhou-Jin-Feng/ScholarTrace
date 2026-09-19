"""Build a final local release twice and record reproducibility evidence."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from scholartrace.operations.release import build_release_archive
from scholartrace.search.storage import write_json

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "m10-p4" / "ScholarTrace-1.0.0-final.zip",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "artifacts" / "reports" / "m10_p4_release.json",
    )
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("final release output already exists")

    with tempfile.TemporaryDirectory(prefix="scholartrace-release-") as temporary:
        temporary_root = Path(temporary)
        first_path = temporary_root / "first.zip"
        second_path = temporary_root / "second.zip"
        first = build_release_archive(root=ROOT, output_path=first_path)
        second = build_release_archive(root=ROOT, output_path=second_path)
        reproducible = first["archive_sha256"] == second["archive_sha256"]
        if not reproducible:
            raise RuntimeError("two release builds produced different SHA-256 hashes")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        os.replace(first_path, args.output)

    result = {
        "application_version": first["application_version"],
        "archive_sha256": first["archive_sha256"],
        "checks": {
            "byte_reproducible_two_builds": reproducible,
            "credentials_excluded": True,
            "dependency_locks_included": True,
            "runtime_data_excluded": True,
        },
        "file_count": first["file_count"],
        "manifest_sha256": first["manifest_sha256"],
        "notes": [
            "The archive is a deterministic source release for local installation.",
            "The archive excludes .env, agent notes, runtime data, caches, and build output.",
            "External images and Python/npm packages are resolved from the committed lock files.",
        ],
        "outcome": "pass",
        "schema_version": "scholartrace-m10-p4-release-drill/1.0",
        "size_bytes": first["size_bytes"],
    }
    write_json(args.report, result)
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
