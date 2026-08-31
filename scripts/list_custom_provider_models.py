"""List models from the configured OpenAI-compatible provider without inference calls."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import httpx

from scholartrace.model_provider import (
    OpenAICompatibleCatalogClient,
    ProviderCatalogError,
    read_dotenv,
    resolve_provider_settings,
)

ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read GET /v1/models from a custom OpenAI-compatible provider."
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Provider root URL; /v1/models is appended unless /v1 is already present.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
    )
    parser.add_argument("--api-key", default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--model",
        default=None,
        help="Optional model ID to record for a later smoke.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=ROOT / ".env",
        help="Local dotenv file; defaults to the project-root .env.",
    )
    return parser.parse_args()


async def run(base_url: str, timeout_seconds: float, api_key: str) -> int:
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        catalog = OpenAICompatibleCatalogClient(
            client=client,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
        )
        models = await catalog.list_models(api_key)
    if not models:
        print("No models were returned by the provider.")
        return 0
    print("model_id\towned_by\tcreated\tcontext_window")
    for model in models:
        print(
            f"{model.model_id}\t{model.owned_by or '-'}\t"
            f"{model.created if model.created is not None else '-'}\t"
            f"{model.context_window if model.context_window is not None else '-'}"
        )
    return 0


def main() -> int:
    args = parse_args()
    try:
        dotenv_values = read_dotenv(args.env_file)
        settings = resolve_provider_settings(
            cli_values={
                "base_url": args.base_url,
                "api_key": args.api_key,
                "timeout_seconds": str(args.timeout_seconds)
                if args.timeout_seconds is not None
                else None,
                "model": args.model,
            },
            environment=os.environ,
            dotenv_values=dotenv_values,
        )
    except ValueError as exc:
        print(f"Provider configuration invalid: {exc}")
        return 1
    api_key = settings.api_key
    if not api_key.strip():
        print(
            "SCHOLARTRACE_API_KEY is not set; no request was made. "
            "Set it in the local environment and rerun this read-only probe."
        )
        return 2
    try:
        return asyncio.run(run(settings.base_url, settings.timeout_seconds, api_key))
    except (ProviderCatalogError, ValueError) as exc:
        print(f"Model catalog unavailable: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
