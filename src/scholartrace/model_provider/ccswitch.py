"""Load the current Codex provider selected in cc-switch without exposing credentials."""

from __future__ import annotations

import json
import sqlite3
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .settings import ProviderSettings

DEFAULT_CCSWITCH_DB = Path.home() / ".cc-switch" / "cc-switch.db"
DEFAULT_CCSWITCH_LUNA_PROVIDER_ID = "sub2api-1789904127847"


def load_ccswitch_codex_settings(
    *,
    provider_id: str = DEFAULT_CCSWITCH_LUNA_PROVIDER_ID,
    model_override: str | None = None,
    reasoning_effort: str | None = None,
    db_path: Path = DEFAULT_CCSWITCH_DB,
    timeout_seconds: float = 20.0,
) -> tuple[ProviderSettings, dict[str, Any]]:
    """Load one Codex cc-switch profile while keeping the key private."""

    if not db_path.is_file():
        raise ValueError(f"cc-switch database is missing: {db_path}")
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "select name, notes, website_url, settings_config "
            "from providers where id=? and app_type='codex'",
            (provider_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError("requested cc-switch Codex provider was not found")
    name, notes, website_url, raw_settings = row
    if notes not in {"GPT", "Luna"}:
        raise ValueError("selected cc-switch provider is not a GPT/Luna Codex profile")
    payload = json.loads(raw_settings)
    config = tomllib.loads(payload["config"])
    provider_config = config["model_providers"]["custom"]
    base_url = provider_config.get("base_url")
    api_key = payload.get("auth", {}).get("OPENAI_API_KEY", "")
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("cc-switch provider base URL is missing")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("cc-switch provider API key is missing")
    configured_model = config.get("model")
    model = model_override or configured_model
    if not isinstance(model, str) or not model.strip():
        raise ValueError("cc-switch provider model is missing")
    settings = ProviderSettings(
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        model=model,
        structured_output_mode="auto",
    )
    metadata: dict[str, Any] = {
        "provider_source": "cc-switch",
        "provider_id": provider_id,
        "provider_name": name,
        "provider_notes": notes,
        "provider_website": website_url,
        "provider_hostname": urlparse(settings.base_url).hostname,
        "configured_model": configured_model,
        "selected_model": model,
        "api_key_present": True,
        "reasoning_effort": reasoning_effort,
    }
    return settings, metadata
