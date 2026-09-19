from __future__ import annotations

import asyncio
import json
import tomllib
from pathlib import Path

import httpx
import pytest

from scholartrace import __version__
from scholartrace.api.app import create_app
from scholartrace.delivery.service import M6TaskService
from scholartrace.operations.release import _is_excluded

ROOT = Path(__file__).resolve().parents[1]


def test_relative_storage_is_rooted_at_project_not_cwd(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    service = M6TaskService(root=project, data_dir=Path("data/tasks"))
    try:
        assert (project / "data/tasks/tasks.sqlite").is_file()
        assert not (elsewhere / "data").exists()
    finally:
        service.close()


def test_nested_tool_state_is_not_packaged(tmp_path) -> None:
    for name in (".monkeycode", ".ohmyagent", ".serena", ".claude", ".kilo", "graphify-out"):
        assert _is_excluded(tmp_path / "docs" / name / "state.json", tmp_path)


def test_application_versions_agree_without_changing_frozen_examples() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text("utf-8"))
    frontend = json.loads((ROOT / "frontend/package.json").read_text("utf-8"))
    frontend_lock = json.loads((ROOT / "frontend/package-lock.json").read_text("utf-8"))
    assert project["project"]["version"] == __version__ == frontend["version"]
    assert frontend_lock["version"] == frontend_lock["packages"][""]["version"] == __version__
    assert next(p for p in lock["package"] if p["name"] == "scholartrace")["version"] == __version__


@pytest.mark.parametrize("explicit", [False, True])
def test_default_and_absolute_storage_paths_remain_supported(tmp_path, explicit) -> None:
    project = tmp_path / "project"
    project.mkdir()
    absolute = tmp_path / "separate-data"
    service = M6TaskService(root=project, data_dir=absolute if explicit else None)
    try:
        expected = absolute if explicit else project / "artifacts/m6-delivery"
        assert (expected / "tasks.sqlite").is_file()
    finally:
        service.close()


def test_api_and_health_use_application_version(tmp_path) -> None:
    app = create_app(root=ROOT, data_dir=tmp_path / "api")

    async def check() -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/v1/health/live")).json()["version"] == __version__
            assert (await client.get("/openapi.json")).json()["info"]["version"] == __version__

    try:
        asyncio.run(check())
    finally:
        app.state.m6_service.close()
