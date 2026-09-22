"""Run local checks and persist their actual results against a source snapshot."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from scholartrace.search.storage import write_json
from scholartrace.verification_ablation.validation import source_snapshot

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "agent" / "validation" / "verification-ablation"
OUTPUT = ROOT / "evaluation/reports/verification_ablation_validation.json"


def public_content_check() -> list[str]:
    """Check public text only; retain technical evaluation and restart terminology."""
    patterns = [
        re.compile(r"\u7b80\u5386|\u9762\u8bd5|\u5b66\u4e60|\u7ec3\u4e60|\u4f5c\u54c1\u96c6"),
        re.compile(r"portfolio_assessment|personal-project|A-minus-to-A|A-to-A-plus", re.I),
        re.compile(r"A-\s*(?:->|→)\s*A|A\s*(?:->|→)\s*A\+"),
        re.compile(r"sk-[A-Za-z0-9]{24,}|Bearer\s+[A-Za-z0-9._-]{24,}"),
        re.compile(r"C:[/\\]Users[/\\](?!Public\b)[^/\\\s]+", re.I),
    ]
    files = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    ).splitlines()
    findings = []
    for name in set(files):
        path = ROOT / name
        if not path.is_file() or path.suffix not in {".py", ".md", ".json", ".toml", ".txt"}:
            continue
        if path.resolve() == Path(__file__).resolve():
            continue  # The scanner necessarily contains its own matching expressions.
        for number, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if any(pattern.search(line) for pattern in patterns):
                findings.append(f"{name}:{number}")
    return sorted(findings)


def main() -> int:
    PRIVATE.mkdir(parents=True, exist_ok=True)
    snapshot = source_snapshot(ROOT)
    junit = PRIVATE / "pytest.xml"
    python = sys.executable
    commands = [
        (
            "pytest",
            [
                python,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                "artifacts/verification-ablation-validation",
                "--junitxml",
                str(junit),
            ],
        ),
        ("ruff", [python, "-m", "ruff", "check", "--no-cache", "src", "scripts", "tests"]),
        (
            "mypy",
            [
                python,
                "-m",
                "mypy",
                "src/scholartrace",
                "scripts/build_verification_ablation_review.py",
                "scripts/validate_verification_ablation.py",
                "scripts/run_sa05_formal.py",
            ],
        ),
        ("compileall", [python, "-m", "compileall", "-q", "src", "scripts"]),
        ("diff_check", ["git", "diff", "--check"]),
    ]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    checks = []
    for name, command in commands:
        started = datetime.now(UTC)
        completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True)
        (PRIVATE / f"{name}.log").write_bytes(completed.stdout + completed.stderr)
        display = [
            "python"
            if arg == python
            else Path(arg).relative_to(ROOT).as_posix()
            if arg == str(junit)
            else arg
            for arg in command
        ]
        checks.append(
            {
                "name": name,
                "command": display,
                "started_at": started.isoformat(),
                "completed_at": datetime.now(UTC).isoformat(),
                "exit_code": completed.returncode,
            }
        )
        print(f"{name}: exit {completed.returncode}", flush=True)
    findings = public_content_check()
    checks.append(
        {
            "name": "public_content",
            "command": ["public_content_check"],
            "exit_code": int(bool(findings)),
            "findings": findings,
        }
    )
    tests = {}
    if junit.is_file():
        suites = ET.parse(junit).getroot().findall("testsuite")
        tests = {
            key: sum(int(s.attrib.get(key, 0)) for s in suites)
            for key in ("tests", "failures", "errors", "skipped")
        }
    unchanged = snapshot == source_snapshot(ROOT)
    passed = unchanged and all(c["exit_code"] == 0 for c in checks)
    write_json(
        OUTPUT,
        {
            "schema_version": "1.0",
            "recorded_at": datetime.now(UTC).isoformat(),
            "source_sha256": snapshot,
            "source_unchanged_during_checks": unchanged,
            "status": "passed" if passed else "failed",
            "checks": checks,
            "pytest": tests,
            "scope": "Local checks only; live integrations are not covered.",
        },
    )
    print(json.dumps({"passed": passed, "pytest": tests, "public_content": findings}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
