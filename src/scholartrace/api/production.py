"""Explicit production ASGI factory without import-time stores or network access."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from scholartrace.api.factory import create_app
from scholartrace.contracts import BudgetLimits
from scholartrace.delivery.authorization import RuntimePolicy
from scholartrace.delivery.production import ProductionResearchRunner


class ProductionConfigurationError(ValueError):
    """Configuration cannot be used; no raw configuration is echoed."""


class ProductionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    policy: RuntimePolicy
    data_dir: str = Field(min_length=1)
    year_from: int = Field(ge=1900, le=2100)
    retrieval_cutoff: date
    plan_limits: BudgetLimits

    @model_validator(mode="after")
    def consistent_scope(self) -> ProductionSettings:
        if self.year_from > self.retrieval_cutoff.year:
            raise ValueError("invalid research year range")
        if self.policy.local is None or self.policy.documind_url is None:
            raise ValueError("local analysis and DocuMind configuration required")
        if self.plan_limits.max_fulltext_papers > self.policy.max_papers:
            raise ValueError("plan paper limit exceeds configured policy")
        return self


def configured_app(
    config_path: Path, *, api_key: str, root: Path | None = None,
    deployment_mode: str = "loopback", auth_token: str | None = None,
) -> FastAPI:
    """Load the named non-secret JSON and assemble, without approving any task."""
    if not api_key.strip() or any(c.isspace() for c in api_key):
        raise ProductionConfigurationError("a nonblank model credential is required")
    try:
        with config_path.open("rb") as stream:
            raw = stream.read(128 * 1024 + 1)
        if len(raw) > 128 * 1024:
            raise ProductionConfigurationError("production configuration exceeds size limit")
        settings = ProductionSettings.model_validate_json(raw)
    except (OSError, ValidationError):
        raise ProductionConfigurationError(
            "cannot load production configuration; check the explicit JSON file and schema"
        ) from None
    data_dir = Path(settings.data_dir)
    if not data_dir.is_absolute():
        data_dir = config_path.resolve().parent / data_dir
    runner = ProductionResearchRunner(
        policy=settings.policy, api_key=api_key, data_dir=data_dir,
        year_from=settings.year_from, retrieval_cutoff=settings.retrieval_cutoff,
        plan_limits=settings.plan_limits,
    )
    return create_app(
        root=root, data_dir=data_dir, live_runner=runner,
        runtime_policy=settings.policy, worker_count=1,
        deployment_mode=deployment_mode, auth_token=auth_token,
    )


def create_production_app(environ: Mapping[str, str] | None = None) -> FastAPI:
    """Uvicorn --factory entry; reads only explicitly named environment settings."""
    values = os.environ if environ is None else environ
    config = values.get("SCHOLARTRACE_RUNTIME_CONFIG", "").strip()
    if not config:
        raise ProductionConfigurationError("SCHOLARTRACE_RUNTIME_CONFIG is required")
    return configured_app(
        Path(config), api_key=values.get("SCHOLARTRACE_MODEL_API_KEY", ""),
        deployment_mode=values.get("SCHOLARTRACE_DEPLOYMENT_MODE", "loopback"),
        auth_token=values.get("SCHOLARTRACE_AUTH_TOKEN", ""),
    )
