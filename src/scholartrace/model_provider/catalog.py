"""Read-only model catalog client for OpenAI-compatible providers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx


class ProviderCatalogError(RuntimeError):
    """The provider model catalog was unavailable or outside the expected contract."""


@dataclass(frozen=True, slots=True)
class ProviderModel:
    """Safe model metadata exposed to selection code and CLI output."""

    model_id: str
    owned_by: str | None = None
    created: int | None = None
    context_window: int | None = None


class OpenAICompatibleCatalogClient:
    """Fetch ``GET /v1/models`` without retaining or logging the API key."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str = "https://www.mxou.ai",
        timeout_seconds: float = 20,
        max_response_bytes: int = 1_000_000,
    ) -> None:
        normalized = base_url.strip().rstrip("/")
        if not normalized:
            raise ValueError("provider base URL must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("provider catalog timeout must be positive")
        if max_response_bytes < 1024:
            raise ValueError("provider catalog response limit must be at least 1024 bytes")
        self.client = client
        self.base_url = normalized
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    async def list_models(self, api_key: str) -> list[ProviderModel]:
        """Return stable, non-sensitive model metadata from the provider."""

        if not api_key.strip():
            raise ValueError("provider API key is required to list models")
        response_url = (
            f"{self.base_url}/models"
            if self.base_url.endswith("/v1")
            else f"{self.base_url}/v1/models"
        )
        try:
            response = await self.client.get(
                response_url,
                headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
                timeout=self.timeout_seconds,
            )
            body = await response.aread()
        except httpx.HTTPError as exc:
            raise ProviderCatalogError("provider model catalog request failed") from exc
        if len(body) > self.max_response_bytes:
            raise ProviderCatalogError("provider model catalog response exceeded size limit")
        if not response.is_success:
            raise ProviderCatalogError(
                f"provider model catalog returned HTTP {response.status_code}"
            )
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ProviderCatalogError("provider model catalog returned invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ProviderCatalogError("provider model catalog is missing a data list")
        models: dict[str, ProviderModel] = {}
        for item in payload["data"]:
            if not isinstance(item, dict):
                raise ProviderCatalogError("provider model catalog contains an invalid item")
            model_id = item.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                raise ProviderCatalogError("provider model catalog item is missing id")
            normalized_id = model_id.strip()
            models.setdefault(
                normalized_id,
                ProviderModel(
                    model_id=normalized_id,
                    owned_by=_optional_text(item.get("owned_by")),
                    created=_optional_int(item.get("created")),
                    context_window=_optional_context_window(item),
                ),
            )
        return [models[model_id] for model_id in sorted(models)]


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _optional_context_window(item: dict[str, Any]) -> int | None:
    for key in ("context_window", "context_length", "max_context_length"):
        value = _optional_int(item.get(key))
        if value is not None:
            return value
    return None
