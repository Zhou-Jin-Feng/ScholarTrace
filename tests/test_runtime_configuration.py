from __future__ import annotations

from scholartrace.verification_ablation.runtime_config import (
    RuntimeConfigurationIdentity,
)


def _identity(**updates: object) -> RuntimeConfigurationIdentity:
    payload: dict[str, object] = {
        "model_profile": "api-strong",
        "model_identifier": "gpt-5.6-luna",
        "provider_protocol": "responses",
        "provider_id": "provider:test",
        "provider_hostname": "provider.example",
        "structured_output_mode": "strict_json_schema",
        "reasoning_effort": "high",
        "streaming": False,
        "compact_context": True,
        "context_transform_id": "compact-v1",
        "report_max_output_tokens": 4_000,
        "verifier_max_output_tokens": 2_000,
        "request_timeout_seconds": 180,
        "max_response_bytes": 1_000_000,
    }
    payload.update(updates)
    return RuntimeConfigurationIdentity.model_validate(payload)


def test_runtime_configuration_changes_produce_distinct_fingerprints() -> None:
    base = _identity()
    variants = [
        _identity(reasoning_effort="max"),
        _identity(streaming=True),
        _identity(compact_context=False, context_transform_id="none"),
        _identity(provider_hostname="other.example"),
        _identity(report_max_output_tokens=8_000),
        _identity(request_timeout_seconds=300),
    ]

    fingerprints = {base.fingerprint_sha256(), *(item.fingerprint_sha256() for item in variants)}

    assert len(fingerprints) == len(variants) + 1
