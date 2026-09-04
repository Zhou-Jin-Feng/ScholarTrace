"""Deterministic source release archive for local ScholarTrace deployments."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
import zipfile
from pathlib import Path
from typing import Any

from scholartrace import __version__

RELEASE_SCHEMA_VERSION = "scholartrace-local-release/1.0"
RELEASE_MANIFEST_NAME = "release-manifest.json"
ROOT_FILES = (
    ".dockerignore",
    ".env.example",
    ".python-version",
    "Dockerfile",
    "PROJECT.md",
    "README.md",
    "docker-compose.yml",
    "pyproject.toml",
    "uv.lock",
)
INCLUDED_DIRECTORIES = (
    ".github",
    "contracts",
    "docs",
    "evaluation",
    "frontend",
    "scripts",
    "src",
    "tests",
)
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "agent",
        "artifacts",
        "data",
        "dist",
        "logs",
        "node_modules",
    }
)
EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo", ".tsbuildinfo"})
EXCLUDED_FILENAMES = frozenset({"m10_p4_release.json"})


class ReleaseError(RuntimeError):
    """A local release archive could not be built safely."""


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _is_excluded(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    parts = {part.lower() for part in relative.parts[:-1]}
    name = relative.name.lower()
    return (
        bool(parts & EXCLUDED_DIRECTORY_NAMES)
        or name == ".env"
        or name in EXCLUDED_FILENAMES
        or name.startswith(".env.")
        and name != ".env.example"
        or path.suffix.lower() in EXCLUDED_SUFFIXES
    )


def _release_files(root: Path) -> list[Path]:
    files: set[Path] = set()
    for raw_path in ROOT_FILES:
        path = root / raw_path
        if not path.is_file():
            raise ReleaseError(f"required release file is missing: {raw_path}")
        files.add(path)
    for raw_directory in INCLUDED_DIRECTORIES:
        directory = root / raw_directory
        if not directory.is_dir():
            raise ReleaseError(f"required release directory is missing: {raw_directory}")
        for path in directory.rglob("*"):
            if path.is_file() and not path.is_symlink() and not _is_excluded(path, root):
                files.add(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def build_release_archive(*, root: Path, output_path: Path) -> dict[str, Any]:
    """Create a byte-reproducible source archive with locked install inputs."""

    project_root = root.resolve()
    archive_path = output_path.resolve()
    if not project_root.is_dir():
        raise ReleaseError("project root does not exist")
    if archive_path.exists():
        raise ReleaseError("release archive already exists")
    try:
        archive_path.relative_to(project_root / "agent")
    except ValueError:
        pass
    else:
        raise ReleaseError("release archive cannot be written under agent/")

    files = _release_files(project_root)
    prefix = f"ScholarTrace-{__version__}"
    entries: list[dict[str, object]] = []
    contents: list[tuple[str, bytes]] = []
    for path in files:
        relative = path.relative_to(project_root).as_posix()
        content = path.read_bytes()
        entries.append(
            {
                "path": relative,
                "sha256": _sha256_bytes(content),
                "size_bytes": len(content),
            }
        )
        contents.append((f"{prefix}/{relative}", content))

    manifest = {
        "application": "ScholarTrace",
        "application_version": __version__,
        "build": {
            "compose_file": "docker-compose.yml",
            "frontend_lock": "frontend/package-lock.json",
            "python_lock": "uv.lock",
            "python_version": "3.11",
        },
        "files": entries,
        "schema_version": RELEASE_SCHEMA_VERSION,
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_path.with_name(f".{archive_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            archive.writestr(_zip_info(f"{prefix}/{RELEASE_MANIFEST_NAME}"), manifest_bytes)
            for archive_name, content in contents:
                archive.writestr(_zip_info(archive_name), content)
        os.replace(temporary, archive_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    return {
        "application_version": __version__,
        "archive_sha256": _sha256_file(archive_path),
        "file_count": len(entries),
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "schema_version": RELEASE_SCHEMA_VERSION,
        "size_bytes": archive_path.stat().st_size,
    }
