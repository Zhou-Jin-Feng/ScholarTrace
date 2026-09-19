"""Safe configuration loading for the opt-in custom model provider."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
StructuredOutputMode = Literal["auto", "json_schema", "json_object"]


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    """Resolved provider settings without exposing the secret in repr/output."""

    base_url: str
    api_key: str = field(repr=False)
    timeout_seconds: float
    model: str | None = None
    structured_output_mode: StructuredOutputMode = "auto"


def read_dotenv(path: Path) -> dict[str, str]:
    """Read a small, deliberately non-interpolating ``.env`` file.

    The project only needs plain ``KEY=VALUE`` entries. Unsupported lines are
    rejected so a typo cannot silently change the provider configuration.
    """

    if not path.exists():
        return {}
    if not path.is_file():
        raise ValueError(f"dotenv path is not a file: {path}")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"invalid dotenv entry at line {line_number}")
        key, value = (part.strip() for part in line.split("=", 1))
        if not _KEY_PATTERN.fullmatch(key):
            raise ValueError(f"invalid dotenv key at line {line_number}")
        values[key] = _parse_dotenv_value(value)
    return values


def resolve_provider_settings(
    *,
    cli_values: Mapping[str, str | None],
    environment: Mapping[str, str],
    dotenv_values: Mapping[str, str],
) -> ProviderSettings:
    """Resolve CLI > environment > dotenv > defaults without logging secrets."""

    base_url = _first_non_empty(
        cli_values.get("base_url"),
        environment.get("SCHOLARTRACE_API_BASE_URL"),
        dotenv_values.get("SCHOLARTRACE_API_BASE_URL"),
        "https://www.mxou.ai",
    )
    api_key = _first_non_empty(
        cli_values.get("api_key"),
        environment.get("SCHOLARTRACE_API_KEY"),
        dotenv_values.get("SCHOLARTRACE_API_KEY"),
        "",
    )
    timeout_text = _first_non_empty(
        cli_values.get("timeout_seconds"),
        environment.get("SCHOLARTRACE_API_TIMEOUT_SECONDS"),
        dotenv_values.get("SCHOLARTRACE_API_TIMEOUT_SECONDS"),
        "20",
    )
    try:
        timeout_seconds = float(timeout_text)
    except ValueError as exc:
        raise ValueError("provider catalog timeout must be a number") from exc
    if timeout_seconds <= 0:
        raise ValueError("provider catalog timeout must be positive")
    model = _first_non_empty(
        cli_values.get("model"),
        environment.get("SCHOLARTRACE_API_MODEL"),
        dotenv_values.get("SCHOLARTRACE_API_MODEL"),
        "",
    )
    structured_output_mode = _first_non_empty(
        cli_values.get("structured_output_mode"),
        environment.get("SCHOLARTRACE_API_STRUCTURED_OUTPUT_MODE"),
        dotenv_values.get("SCHOLARTRACE_API_STRUCTURED_OUTPUT_MODE"),
        "auto",
    )
    if structured_output_mode not in {"auto", "json_schema", "json_object"}:
        raise ValueError("provider structured output mode is invalid")
    return ProviderSettings(
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        model=model or None,
        structured_output_mode=structured_output_mode,  # type: ignore[arg-type]
    )


def _first_non_empty(*values: str | None) -> str:
    for value in values:
        if value is not None and value.strip():
            return value.strip()
    return ""


def _parse_dotenv_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    if value.startswith(("'", '"')):
        raise ValueError("unterminated dotenv quote")
    return value


def resolved_structured_output_mode(
    settings: ProviderSettings,
) -> Literal["json_schema", "json_object"]:
    """Select the safest structured-output dialect for the configured endpoint."""

    if settings.structured_output_mode == "json_schema":
        return "json_schema"
    if settings.structured_output_mode == "json_object":
        return "json_object"
    hostname = (urlparse(settings.base_url).hostname or "").lower()
    return "json_object" if hostname == "api.deepseek.com" else "json_schema"


def uses_deepseek_chat_parameters(settings: ProviderSettings) -> bool:
    """DeepSeek's Chat Completions endpoint uses ``max_tokens``."""

    return (urlparse(settings.base_url).hostname or "").lower() == "api.deepseek.com"
