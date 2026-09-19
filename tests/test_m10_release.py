from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scholartrace.operations.release import ReleaseError, build_release_archive

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_release_archive_is_reproducible_and_excludes_runtime_data(tmp_path: Path) -> None:
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    first_result = build_release_archive(root=ROOT, output_path=first)
    second_result = build_release_archive(root=ROOT, output_path=second)
    assert _sha256(first) == _sha256(second)
    assert first_result["archive_sha256"] == second_result["archive_sha256"]

    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        manifest_name = next(name for name in names if name.endswith("release-manifest.json"))
        manifest = json.loads(archive.read(manifest_name))
    assert manifest["schema_version"] == "scholartrace-local-release/1.0"
    assert any(name.endswith("/uv.lock") for name in names)
    assert any(name.endswith("/.dockerignore") for name in names)
    assert any(name.endswith("/frontend/package-lock.json") for name in names)
    assert all("/agent/" not in name for name in names)
    assert all("/artifacts/" not in name or name.endswith("/artifacts/.gitkeep") for name in names)
    assert all("/data/" not in name or name.endswith("/data/.gitkeep") for name in names)
    assert all("/logs/" not in name or name.endswith("/logs/.gitkeep") for name in names)
    assert all(not name.endswith("/.env") for name in names)
    assert all(not name.endswith("/m10_p4_release.json") for name in names)
    assert all("/node_modules/" not in name for name in names)


def test_release_refuses_to_overwrite_an_existing_archive(tmp_path: Path) -> None:
    output = tmp_path / "existing.zip"
    output.write_bytes(b"keep")
    with pytest.raises(ReleaseError, match="already exists"):
        build_release_archive(root=ROOT, output_path=output)
    assert output.read_bytes() == b"keep"
