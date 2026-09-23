from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scholartrace.model_provider.ccswitch import load_ccswitch_codex_settings


def _write_db(path: Path, *, notes: str = "Luna") -> None:
    connection = sqlite3.connect(path)
    connection.execute(
            "CREATE TABLE providers (id TEXT, app_type TEXT, name TEXT, notes TEXT, "
            "website_url TEXT, settings_config TEXT)"
    )
    config = """
model = "gpt-5.6-luna"
[model_providers.custom]
base_url = "https://provider.test"
"""
    payload = json.dumps(
        {
            "config": config,
            "auth": {"OPENAI_API_KEY": "secret-not-returned"},
        }
    )
    connection.execute(
        "INSERT INTO providers VALUES (?, ?, ?, ?, ?, ?)",
        ("provider-1", "codex", "Current", notes, "https://provider.test", payload),
    )
    connection.commit()
    connection.close()


def test_load_current_ccswitch_provider_without_exposing_key(tmp_path: Path) -> None:
    db = tmp_path / "cc-switch.db"
    _write_db(db)

    settings, metadata = load_ccswitch_codex_settings(
        provider_id="provider-1",
        model_override="gpt-5.6-luna",
        db_path=db,
    )

    assert settings.base_url == "https://provider.test"
    assert settings.model == "gpt-5.6-luna"
    assert settings.api_key == "secret-not-returned"
    assert metadata["provider_source"] == "cc-switch"
    assert metadata["api_key_present"] is True
    assert metadata["reasoning_effort"] is None
    assert "secret-not-returned" not in str(metadata)

    _, high_metadata = load_ccswitch_codex_settings(
        provider_id="provider-1",
        model_override="gpt-5.6-luna",
        reasoning_effort="high",
        db_path=db,
    )
    assert high_metadata["reasoning_effort"] == "high"


def test_ccswitch_loader_rejects_non_codex_notes(tmp_path: Path) -> None:
    db = tmp_path / "cc-switch.db"
    _write_db(db, notes="Other")

    with pytest.raises(ValueError, match="GPT/Luna"):
        load_ccswitch_codex_settings(provider_id="provider-1", db_path=db)
