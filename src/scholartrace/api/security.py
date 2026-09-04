"""Deployment-mode and minimal bearer authentication policy."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from enum import StrEnum

from fastapi import Request


class DeploymentMode(StrEnum):
    """Supported local deployment boundaries."""

    LOOPBACK = "loopback"
    TRUSTED_PRIVATE = "trusted_private"


@dataclass(frozen=True)
class SecurityPolicy:
    """Fail-closed configuration for the task API surface."""

    mode: DeploymentMode
    token: str | None

    @classmethod
    def from_environment(
        cls,
        *,
        deployment_mode: str | None = None,
        auth_token: str | None = None,
    ) -> SecurityPolicy:
        raw_mode = deployment_mode or os.environ.get("SCHOLARTRACE_DEPLOYMENT_MODE", "loopback")
        try:
            mode = DeploymentMode(raw_mode.strip().lower())
        except ValueError as exc:
            raise ValueError(
                "SCHOLARTRACE_DEPLOYMENT_MODE must be loopback or trusted_private"
            ) from exc
        raw_token = (
            auth_token if auth_token is not None else os.environ.get("SCHOLARTRACE_AUTH_TOKEN")
        )
        token = raw_token.strip() if raw_token else None
        if token == "":
            token = None
        if token is not None and (
            len(token) < 16 or any(character.isspace() for character in token)
        ):
            raise ValueError(
                "SCHOLARTRACE_AUTH_TOKEN must be at least 16 non-whitespace characters"
            )
        if mode == DeploymentMode.TRUSTED_PRIVATE and token is None:
            raise ValueError("trusted_private mode requires SCHOLARTRACE_AUTH_TOKEN")
        return cls(mode=mode, token=token)

    @property
    def auth_required(self) -> bool:
        return self.mode == DeploymentMode.TRUSTED_PRIVATE or self.token is not None

    def protects(self, request: Request) -> bool:
        path = request.url.path
        return path == "/api/v1/research/tasks" or path.startswith("/api/v1/research/tasks/")

    def allows(self, request: Request) -> bool:
        if not self.auth_required or not self.protects(request):
            return True
        supplied = self._bearer_token(request.headers.get("Authorization"))
        if (
            supplied is None
            and request.method == "GET"
            and (request.url.path.endswith("/events") or request.url.path.endswith("/report"))
        ):
            supplied = request.query_params.get("access_token")
        return (
            supplied is not None
            and self.token is not None
            and secrets.compare_digest(supplied, self.token)
        )

    @staticmethod
    def _bearer_token(header: str | None) -> str | None:
        if header is None:
            return None
        scheme, separator, value = header.partition(" ")
        if scheme.lower() != "bearer" or not separator or not value or " " in value:
            return None
        return value
