"""Atomic JSON artifact storage and source-tree hashing for reproducible runs."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from pydantic import BaseModel


def write_model(path: Path, model: BaseModel) -> str:
    payload = model.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(path, serialized)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def write_json(path: Path, payload: object) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(path, serialized)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def write_text(path: Path, content: str) -> str:
    _atomic_write(path, content)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary_name = temporary.name
        Path(temporary_name).replace(path)
    finally:
        if temporary_name is not None:
            temporary_path = Path(temporary_name)
            if temporary_path.exists():
                temporary_path.unlink()


def source_tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    paths = sorted(
        path
        for path in (root / "src" / "scholartrace").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
