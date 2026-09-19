from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
import zipfile
from argparse import Namespace
from pathlib import Path

import pytest

from scholartrace.operations.release import (
    INCLUDED_DIRECTORIES,
    ROOT_FILES,
    build_release_archive,
)

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_contract_resolves_reachable_ref_not_frozen_sha(monkeypatch) -> None:
    module = _script("verify_m2_documind_compatibility")
    calls = []

    def git(repo, *args):
        calls.append(args)
        if args[0] == "rev-parse":
            return "a" * 40 + "\n"
        if args[0] == "for-each-ref":
            return "refs/heads/main\n"
        return '__version__ = "2.2.0"\n'

    monkeypatch.setattr(module, "_git", git)
    assert module._current_baseline(ROOT, "main") == ("2.2.0", "a" * 40)
    assert calls[0] == ("rev-parse", "--verify", "main^{commit}")


def test_current_contract_rejects_unreachable_revision(monkeypatch) -> None:
    module = _script("verify_m2_documind_compatibility")
    monkeypatch.setattr(
        module, "_git", lambda repo, *args: "a" * 40 if args[0] == "rev-parse" else ""
    )
    with pytest.raises(ValueError, match="reachable"):
        module._current_baseline(ROOT, "HEAD")


def test_contract_default_is_offline_and_writes_private_report(tmp_path, monkeypatch) -> None:
    module = _script("verify_m2_documind_compatibility")
    output = tmp_path / "current.json"
    monkeypatch.setattr(
        sys, "argv", ["verify", "--provider-repo", str(ROOT), "--output", str(output)]
    )
    monkeypatch.setattr(module, "_current_baseline", lambda *a: ("2.2.0", "a" * 40))
    monkeypatch.setattr(module, "_validate_baseline", lambda *a: {"passed": True})
    monkeypatch.setattr(module, "_git", lambda *a: "")
    monkeypatch.setattr(module, "_online_readiness", lambda *a: pytest.fail("unexpected network"))
    assert module.main() == 0
    summary = json.loads(output.read_text("utf-8"))
    assert summary["scope"] == "current_contract"
    assert summary["online_readiness"]["checked"] is False
    assert module.DEFAULT_OUTPUT.is_relative_to(ROOT / "artifacts")


def test_explicit_online_failure_cannot_be_reported_as_pass(tmp_path, monkeypatch) -> None:
    module = _script("verify_m2_documind_compatibility")
    output = tmp_path / "online.json"
    monkeypatch.setattr(
        sys, "argv", ["verify", "--base-url", "http://provider.invalid", "--output", str(output)]
    )
    monkeypatch.setattr(module, "_current_baseline", lambda *a: ("2.2.0", "a" * 40))
    monkeypatch.setattr(module, "_validate_baseline", lambda *a: {"passed": True})
    monkeypatch.setattr(module, "_git", lambda *a: "")
    monkeypatch.setattr(
        module, "_online_readiness", lambda *a: {"checked": True, "compatible": False}
    )
    assert module.main() == 1
    assert json.loads(output.read_text("utf-8"))["passed"] is False


def test_contract_schema_failure_is_sanitized(tmp_path, monkeypatch) -> None:
    module = _script("verify_m2_documind_compatibility")
    output = tmp_path / "invalid.json"
    monkeypatch.setattr(sys, "argv", ["verify", "--output", str(output)])
    monkeypatch.setattr(module, "_current_baseline", lambda *a: ("2.2.0", "a" * 40))

    def invalid(*args):
        raise module.SchemaValidationError("private sentinel must not appear")

    monkeypatch.setattr(module, "_validate_baseline", invalid)
    assert module.main() == 1
    assert "private sentinel" not in output.read_text("utf-8")


def test_live_smoke_missing_identity_stops_before_fixture_or_network() -> None:
    module = _script("run_m2_live_smoke")
    with pytest.raises(ValueError, match="provider identity"):
        asyncio.run(module._run(Namespace(documind_version="2.2.0", documind_commit="")))


@pytest.mark.parametrize("commit", ["212f60a", "", "z" * 40])
def test_live_smoke_rejects_incomplete_provider_identity(commit) -> None:
    module = _script("run_m2_live_smoke")
    with pytest.raises(ValueError, match="provider identity"):
        module._validate_provider_identity("2.2.0", commit)


def test_live_smoke_accepts_explicit_supported_identity() -> None:
    module = _script("run_m2_live_smoke")
    module._validate_provider_identity("2.2.0", "c" * 40)


def test_real_archive_excludes_nested_private_files_and_keeps_empty_roots(tmp_path) -> None:
    project = tmp_path / "project"
    for name in ROOT_FILES:
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("public", "utf-8")
    for name in INCLUDED_DIRECTORIES:
        (project / name).mkdir(parents=True, exist_ok=True)
    for name in (
        "docs/.monkeycode/state.json",
        "src/.ohmyagent/cache.json",
        "tests/agent/private.md",
        "frontend/.env.local",
        "data/tasks.db",
        "logs/api.log",
        "artifacts/private.json",
    ):
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("PRIVATE_SENTINEL", "utf-8")
    output = tmp_path / "release.zip"
    build_release_archive(root=project, output_path=output)
    with zipfile.ZipFile(output) as archive:
        assert not any(b"PRIVATE_SENTINEL" in archive.read(n) for n in archive.namelist())
        for name in ("data", "logs", "artifacts"):
            assert any(n.endswith(f"/{name}/.gitkeep") for n in archive.namelist())


def test_git_ignores_runtime_and_nested_tooling_but_not_placeholders(tmp_path) -> None:
    project = tmp_path / "ignore-check"
    project.mkdir()
    (project / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
    subprocess.run(["git", "init", "--quiet", str(project)], check=True, capture_output=True)
    for name in (
        "data/tasks.db",
        "logs/api.log",
        "artifacts/reports/new.json",
        "docs/.monkeycode/state.json",
        "src/.ohmyagent/cache.json",
        "agent/note.md",
    ):
        result = subprocess.run(["git", "check-ignore", "--quiet", name], cwd=project)
        assert result.returncode == 0, name
    for name in ("data/.gitkeep", "logs/.gitkeep", "artifacts/.gitkeep"):
        result = subprocess.run(["git", "check-ignore", "--quiet", name], cwd=project)
        assert result.returncode == 1, name


@pytest.mark.parametrize(
    "script,report",
    [
        ("run_m6_demo", "m6_demo_smoke.json"),
        ("run_m10_p1_load", "m10_p1_local_load.json"),
    ],
)
def test_default_report_creates_missing_private_parent(
    script, report, tmp_path, monkeypatch
) -> None:
    module = _script(script)

    async def run(*args):
        return {"passed": True}

    monkeypatch.setattr(module, "_run", run)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "scripts" / (script + ".py")))
    monkeypatch.setattr(sys, "argv", [script])
    module.main()
    assert json.loads((tmp_path / "artifacts/reports" / report).read_text("utf-8"))["passed"]
