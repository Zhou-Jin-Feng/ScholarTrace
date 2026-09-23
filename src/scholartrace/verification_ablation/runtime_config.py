"""Runtime provider configuration identity for reproducible ablations."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .models import (
    AblationModel,
    ProviderProtocol,
    ReasoningEffort,
    canonical_sha256,
)


class RuntimeConfigurationIdentity(AblationModel):
    """Hash the provider/runtime settings that affect an executed request."""

    schema_version: Literal["1.0"] = "1.0"
    model_profile: str
    model_identifier: str
    provider_protocol: ProviderProtocol
    provider_id: str
    provider_hostname: str
    structured_output_mode: str | None = None
    reasoning_effort: ReasoningEffort
    automatic_retry: Literal[False] = False
    streaming: bool
    compact_context: bool
    context_transform_id: str
    report_max_output_tokens: int = Field(ge=1)
    verifier_max_output_tokens: int = Field(ge=1)
    request_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    max_response_bytes: int = Field(ge=1024)

    def fingerprint_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def runtime_configuration_payload(
    identity: RuntimeConfigurationIdentity,
) -> dict[str, object]:
    """Return a serializable identity payload with its stable fingerprint."""

    return {
        **identity.model_dump(mode="json"),
        "fingerprint_sha256": identity.fingerprint_sha256(),
    }
