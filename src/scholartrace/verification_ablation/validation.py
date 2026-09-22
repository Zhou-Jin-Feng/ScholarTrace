"""Bind validation results to the exact source, tests and documentation snapshot."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def source_snapshot(root: Path) -> dict[str, str]:
    paths = [root / name for name in ("pyproject.toml", "uv.lock", "README.md", "CHANGELOG.md")]
    for directory in ("src", "scripts", "tests", "docs", "evaluation/seeds"):
        paths.extend(
            p
            for p in (root / directory).rglob("*")
            if p.is_file() and p.suffix in {".py", ".md", ".json", ".toml"}
        )
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(set(paths))
        if p.is_file()
    }


def validation_status(root: Path, path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"status": "unverified", "reason": "validation record missing"}
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("source_sha256") != source_snapshot(root):
        return {"status": "stale", "reason": "source snapshot changed"}
    checks = record.get("checks", [])
    required = {"pytest", "ruff", "mypy", "compileall", "diff_check", "public_content"}
    if not required <= {c.get("name") for c in checks}:
        return {"status": "unverified", "reason": "required checks missing"}
    passed = (
        record.get("source_unchanged_during_checks") is True
        and record.get("status") == "passed"
        and all(c.get("exit_code") == 0 for c in checks)
    )
    return {
        "status": "passed" if passed else "failed",
        "record_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "recorded_at": record.get("recorded_at"),
        "checks": checks,
    }
