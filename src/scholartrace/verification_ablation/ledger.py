"""Execution accounting and pre-dispatch budgets for ablation requests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .models import (
    AblationAttemptRecord,
    AblationExecutionError,
    AblationExecutionManifest,
    AblationUsage,
    AblationVariant,
)


def execution_usage(
    manifest: AblationExecutionManifest,
    attempts: Sequence[AblationAttemptRecord],
    legacy: Mapping[AblationVariant, AblationUsage],
) -> dict[AblationVariant, AblationUsage]:
    """Count every sent request once; final result rows are not the execution ledger."""
    output: dict[AblationVariant, AblationUsage] = {}
    for variant in ("V-on", "V-off"):
        values = legacy[variant].model_dump()
        for attempt in attempts:
            if attempt.variant != variant or attempt.attempted_calls == 0:
                continue
            verifier = attempt.operation == "verifier"
            prefix = "verifier_" if verifier else ""
            count_field = "verifier_attempted_calls" if verifier else "report_calls"
            values[count_field] += attempt.attempted_calls
            if verifier:
                values["verifier_calls"] += attempt.successful_calls
            elif manifest.execution_mode == "production":
                values["model_calls"] = (values["model_calls"] or 0) + attempt.attempted_calls
                values["provider_api_calls"] = (
                    values["provider_api_calls"] or 0
                ) + attempt.attempted_calls
            for field, value in (
                ("input_tokens", attempt.input_tokens),
                ("output_tokens", attempt.output_tokens),
                ("reference_cost_cny", attempt.actual_reference_cost_cny),
                ("duration_seconds", attempt.duration_seconds),
            ):
                key = prefix + field
                values[key] = (values[key] or 0) + (value or 0)
            unknown = any(
                value is None
                for value in (
                    attempt.input_tokens,
                    attempt.output_tokens,
                    attempt.actual_reference_cost_cny,
                )
            )
            if unknown:
                values["unknown_attempts"] += attempt.attempted_calls
                if attempt.actual_reference_cost_cny is None:
                    values["unknown_reserved_reference_cost_cny"] += max(
                        attempt.reserved_reference_cost_cny or 0,
                        manifest.budget.unknown_attempt_reserve_cny * attempt.attempted_calls,
                    )
        output[variant] = AblationUsage.model_validate(values)
    return output


def check_dispatch(
    manifest: AblationExecutionManifest,
    attempts: Sequence[AblationAttemptRecord],
    legacy: Mapping[AblationVariant, AblationUsage],
    candidate: AblationAttemptRecord,
) -> None:
    budget = manifest.budget
    usages = execution_usage(manifest, attempts, legacy)
    selected = (
        list(usages.values()) if budget.budget_scope == "shared" else [usages[candidate.variant]]
    )
    prior_attempts = [
        a
        for a in attempts
        if a.attempted_calls and (budget.budget_scope == "shared" or a.variant == candidate.variant)
    ]

    def total(field: str) -> float:
        return sum(float(getattr(u, field) or 0) for u in selected)

    unknown = total("unknown_attempts")
    if unknown and budget.unknown_usage_policy == "stop":
        raise AblationExecutionError("budget", "unknown_usage", "unknown cumulative usage")
    if unknown and any(
        (a.input_tokens is None and a.reserved_input_tokens is None)
        or (a.output_tokens is None and a.reserved_output_tokens is None)
        for a in prior_attempts
    ):
        raise AblationExecutionError(
            "budget", "unknown_usage", "unknown token usage has no persisted reservation"
        )
    verifier = candidate.operation == "verifier"
    paid = int(manifest.execution_mode == "production")
    unknown_input = sum(
        a.reserved_input_tokens or 0 for a in prior_attempts if a.input_tokens is None
    )
    unknown_output = sum(
        a.reserved_output_tokens or 0 for a in prior_attempts if a.output_tokens is None
    )
    checks = (
        (total("verifier_attempted_calls") + int(verifier), budget.max_verifier_calls),
        (total("report_calls") + int(not verifier), budget.max_report_calls),
        (
            total("model_calls") + paid * (total("verifier_attempted_calls") + 1),
            budget.max_model_calls,
        ),
        (
            total("provider_api_calls") + paid * (total("verifier_attempted_calls") + 1),
            budget.max_provider_api_calls,
        ),
        (
            total("input_tokens")
            + total("verifier_input_tokens")
            + unknown_input
            + (candidate.reserved_input_tokens or 0),
            budget.max_input_tokens,
        ),
        (
            total("output_tokens")
            + total("verifier_output_tokens")
            + unknown_output
            + (candidate.reserved_output_tokens or 0),
            budget.max_output_tokens,
        ),
        (
            total("reference_cost_cny")
            + total("verifier_reference_cost_cny")
            + total("unknown_reserved_reference_cost_cny")
            + (candidate.reserved_reference_cost_cny or 0),
            budget.max_reference_cost_cny,
        ),
        (
            total("duration_seconds") + total("verifier_duration_seconds"),
            budget.max_duration_seconds,
        ),
    )
    if any(actual > maximum + 1e-9 for actual, maximum in checks):
        raise AblationExecutionError(
            "budget", "budget_exceeded", "cumulative request budget exhausted before dispatch"
        )
