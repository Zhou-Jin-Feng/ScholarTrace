from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from scholartrace.model_provider.catalog import (
    OpenAICompatibleCatalogClient,
    ProviderCatalogError,
)
from scholartrace.model_provider.settings import read_dotenv, resolve_provider_settings


def test_catalog_lists_models_without_exposing_key() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "gpt-5.2", "owned_by": "provider", "context_window": 400000},
                    {"id": "gpt-5.1", "owned_by": "provider", "created": 1},
                    {"id": "gpt-5.2", "owned_by": "provider"},
                ],
            },
        )

    async def scenario() -> list[object]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = OpenAICompatibleCatalogClient(client=http)
            return await client.list_models("secret-provider-key")

    models = asyncio.run(scenario())
    assert [model.model_id for model in models] == ["gpt-5.1", "gpt-5.2"]
    assert models[1].context_window == 400000
    assert seen == {
        "url": "https://www.mxou.ai/v1/models",
        "authorization": "Bearer secret-provider-key",
    }


def test_catalog_supports_base_url_that_already_contains_v1() -> None:
    async def scenario() -> str:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"data": [{"id": "gpt-test"}]})
            )
        ) as http:
            client = OpenAICompatibleCatalogClient(client=http, base_url="https://example.test/v1")
            await client.list_models("key")
            return "ok"

    assert asyncio.run(scenario()) == "ok"


def test_catalog_requires_key_before_network_call() -> None:
    def unexpected(_: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be called without an API key")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as http:
            client = OpenAICompatibleCatalogClient(client=http)
            await client.list_models(" ")

    with pytest.raises(ValueError, match="API key"):
        asyncio.run(scenario())


def test_catalog_hides_provider_error_body() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    401, content=b"secret-provider-key should never be exposed"
                )
            )
        ) as http:
            client = OpenAICompatibleCatalogClient(client=http)
            await client.list_models("secret-provider-key")

    with pytest.raises(ProviderCatalogError) as caught:
        asyncio.run(scenario())
    assert "secret-provider-key" not in str(caught.value)


def test_dotenv_settings_use_cli_then_environment_then_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SCHOLARTRACE_API_BASE_URL='https://from-file.test'\n"
        "SCHOLARTRACE_API_KEY=file-key\n"
        "SCHOLARTRACE_API_TIMEOUT_SECONDS=12\n"
        "SCHOLARTRACE_API_MODEL=gpt-file\n",
        encoding="utf-8",
    )
    settings = resolve_provider_settings(
        cli_values={"base_url": None, "api_key": None, "timeout_seconds": None, "model": None},
        environment={
            "SCHOLARTRACE_API_KEY": "environment-key",
            "SCHOLARTRACE_API_MODEL": "gpt-env",
        },
        dotenv_values=read_dotenv(env_file),
    )
    assert settings.base_url == "https://from-file.test"
    assert settings.api_key == "environment-key"
    assert settings.timeout_seconds == 12
    assert settings.model == "gpt-env"
    assert "environment-key" not in repr(settings)

    overridden = resolve_provider_settings(
        cli_values={
            "base_url": "https://from-cli.test",
            "api_key": "cli-key",
            "timeout_seconds": "3.5",
            "model": "gpt-cli",
        },
        environment={"SCHOLARTRACE_API_KEY": "environment-key"},
        dotenv_values=read_dotenv(env_file),
    )
    assert overridden == type(overridden)("https://from-cli.test", "cli-key", 3.5, "gpt-cli")


def test_dotenv_rejects_malformed_entries(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SCHOLARTRACE_API_KEY\n", encoding="utf-8")
    with pytest.raises(ValueError, match="dotenv"):
        read_dotenv(env_file)


def test_settings_reject_invalid_timeout() -> None:
    with pytest.raises(ValueError, match="timeout"):
        resolve_provider_settings(
            cli_values={"timeout_seconds": "not-a-number"},
            environment={},
            dotenv_values={},
        )
