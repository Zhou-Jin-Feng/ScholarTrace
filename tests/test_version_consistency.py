from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_python_and_core_dependency_versions_are_frozen() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    assert project["requires-python"] == ">=3.11,<3.12"
    assert project["version"] == "1.0.0"
    assert "langgraph==1.2.11" in project["dependencies"]
    assert "langgraph-checkpoint-sqlite==3.1.1" in project["dependencies"]
    assert "pydantic==2.13.4" in project["dependencies"]
    assert "fastapi==0.141.1" in project["dependencies"]
    assert "networkx==3.6.1" in project["dependencies"]


def test_run_manifest_example_matches_upstream_freeze() -> None:
    bundle = json.loads((ROOT / "contracts/examples/m0_bundle.json").read_text("utf-8"))
    baselines = {item["service"]: item for item in bundle["RunManifest"]["service_baselines"]}
    assert baselines["documind"]["version"] == "2.1.0"
    assert baselines["documind"]["git_commit"].startswith("32c5eb8")
    assert baselines["scholargraph"]["version"] == "1.0.0"
    assert baselines["scholargraph"]["git_commit"].startswith("953e40b")
