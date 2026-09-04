from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED_SUFFIXES = {".json", ".md", ".py", ".toml", ".ps1"}
SECRET_PATTERNS = (
    re.compile(r"(?<![A-Za-z])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|password|smtp[_-]?token)\s*=\s*['\"][^'\"]{8,}"),
)


def test_private_runtime_paths_are_ignored() -> None:
    ignored = (ROOT / ".gitignore").read_text("utf-8")
    for entry in (
        "agent/",
        ".venv/",
        ".env",
        "data/",
        "logs/",
        "artifacts/",
        "/dist/",
    ):
        assert entry in ignored


def test_frontend_entrypoint_mounts_the_root_component() -> None:
    html = (ROOT / "frontend" / "index.html").read_text("utf-8")
    entrypoint = (ROOT / "frontend" / "src" / "main.tsx").read_text("utf-8")
    assert '<div id="root"></div>' in html
    assert 'document.getElementById("root")' in entrypoint
    assert "createRoot(rootElement).render(<App />)" in entrypoint


def test_no_obvious_credentials_in_committable_text() -> None:
    findings: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if any(part in {"agent", ".venv", ".git"} for part in path.parts):
            continue
        text = path.read_text("utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(str(path.relative_to(ROOT)))
    assert not findings, f"possible credentials found in: {findings}"
