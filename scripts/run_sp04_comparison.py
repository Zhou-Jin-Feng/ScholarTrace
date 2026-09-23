"""Run the authorized SP-04 three-scheme comparison through the current Codex
cc-switch Luna provider."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import httpx

from scholartrace.model_provider import (
    ApiCallBudget,
    ApiCallCounter,
    OpenAICompatibleSemanticVerifier,
    load_ccswitch_codex_settings,
)
from scholartrace.model_provider.settings import resolved_structured_output_mode
from scholartrace.search.storage import source_tree_sha256, write_json
from scholartrace.verification_ablation import (
    ABLATION_REPORT_SCHEMA_SHA256,
    AblationBudget,
    AblationExecutionManifest,
    AblationPrivateRow,
    AblationResult,
    CompactReportContextGenerator,
    FormalCheckpointRecorder,
    OpenAICompatibleAblationReportGenerator,
    VerificationAblationRunner,
    write_artifacts,
)
from scholartrace.verification_ablation.models import (
    AblationAttemptRecord,
    AblationFrozenInput,
    AblationReportGenerator,
    ReasoningEffort,
)
from scholartrace.verification_ablation.provider_context import COMPACT_CONTEXT_ID
from scholartrace.verification_ablation.runtime_config import (
    RuntimeConfigurationIdentity,
    runtime_configuration_payload,
)
from scholartrace.verification_ablation.simple_baseline import (
    SimpleBaselineArchive,
    SimpleBaselineManifest,
    SimpleBaselineRunner,
)
from scholartrace.verification_ablation.simple_baseline import (
    write_artifacts as write_simple_artifacts,
)
from scholartrace.verification_ablation.stage_two_selection import should_verify

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_INPUTS = ROOT / "agent" / "verification-ablation" / "SP-02" / "frozen_inputs.json"
SA02_MANIFEST = ROOT / "evaluation" / "seeds" / "sp_02_new_question_manifest.json"
PRIVATE_ROOT = ROOT / "agent" / "verification-ablation" / "SP-04-comparison"
PUBLIC_ROOT = ROOT / "evaluation" / "reports"
PRE_RECOVERY_SNAPSHOT = ROOT / "agent" / "provider-review-20260923" / "checkpoint-summary.json"
MODEL = "gpt-5.6-luna"
REPORT_MAX_OUTPUT_TOKENS = 4_000
VERIFIER_MAX_OUTPUT_TOKENS = 2_000
REQUEST_TIMEOUT_SECONDS = 180.0
MAX_RESPONSE_BYTES = 1_000_000
DEFAULT_PROVIDER_ID = "sub2api-1789904127847"
LUNA_INPUT_USD_PER_MILLION = 0.2
LUNA_OUTPUT_USD_PER_MILLION = 1.2
LUNA_USD_TO_CNY = 7.5
BASELINE_COST_SHARE = 0.20
FULL_COST_SHARE = 0.50
SELECTIVE_COST_SHARE = 0.30


def public_path_string(path: Path) -> str:
    """Return a stable repository-relative path for public summaries."""

    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def current_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    ).strip()


def load_stage_two_inputs() -> dict[str, AblationFrozenInput]:
    payload = json.loads(PRIVATE_INPUTS.read_text(encoding="utf-8"))
    inputs = {
        item["input"]["question_id"]: AblationFrozenInput.model_validate(item["input"])
        for item in payload["questions"]
    }
    if len(inputs) != 8:
        raise ValueError(f"expected 8 SP-02 inputs, got {len(inputs)}")
    return dict(sorted(inputs.items()))


def enabled_policy(model: str) -> Any:
    payload = json.loads(
        (ROOT / "contracts" / "examples" / "m0_bundle.json").read_text(encoding="utf-8")
    )["ModelRoutingPolicy"]
    for profile in payload["profiles"]:
        if profile["profile_id"] == "api-strong":
            profile.update(
                {
                    "provider": "custom-openai-compatible",
                    "model_name": model,
                    "model_version": model,
                    "context_window": 400_000,
                    "enabled": True,
                }
            )
    payload["paid_routes_enabled"] = True
    from scholartrace.contracts import ModelRoutingPolicy

    return ModelRoutingPolicy.model_validate(payload)


def simple_manifest(
    inputs: Mapping[str, AblationFrozenInput],
    settings: Any,
    reasoning_effort: ReasoningEffort,
    streaming: bool,
) -> SimpleBaselineManifest:
    public = json.loads(SA02_MANIFEST.read_text(encoding="utf-8"))
    return SimpleBaselineManifest(
        experiment_id="scholartrace-sp04-simple-baseline-production-v1",
        execution_mode="production",
        baseline_commit=current_commit(),
        source_tree_sha256=source_tree_sha256(ROOT),
        model_profile="api-strong",
        model_identifier=MODEL,
        provider_protocol="responses",
        report_prompt_sha256=OpenAICompatibleAblationReportGenerator.prompt_template_sha256,
        report_schema_sha256=ABLATION_REPORT_SCHEMA_SHA256,
        report_length_limit_chars=5_000,
        reasoning_effort=reasoning_effort,
        provider_hostname=urlparse(settings.base_url).hostname,
        structured_output_mode=resolved_structured_output_mode(settings),
        streaming=streaming,
        dataset_fingerprint_sha256=public["question_set_fingerprint_sha256"],
        question_input_sha256={
            question_id: item.input_sha256 for question_id, item in inputs.items()
        },
        max_context_characters=40_000,
    )


def ablation_manifest(
    *,
    inputs: Mapping[str, AblationFrozenInput],
    settings: Any,
    experiment_id: str,
    max_verifier_calls: int,
    allocated_cost_cny: float,
    reasoning_effort: ReasoningEffort,
    streaming: bool,
) -> AblationExecutionManifest:
    public = json.loads(SA02_MANIFEST.read_text(encoding="utf-8"))
    report_calls = len(inputs)
    verifier_input_upper = 20_000
    report_input_upper = 30_000
    verifier_output = VERIFIER_MAX_OUTPUT_TOKENS
    report_output = REPORT_MAX_OUTPUT_TOKENS
    return AblationExecutionManifest(
        experiment_id=experiment_id,
        execution_mode="production",
        baseline_commit=current_commit(),
        model_profile="api-strong",
        model_identifier=MODEL,
        provider_protocol="responses",
        verifier_prompt_sha256=OpenAICompatibleSemanticVerifier.prompt_template_sha256,
        report_prompt_sha256=OpenAICompatibleAblationReportGenerator.prompt_template_sha256,
        report_schema_sha256=ABLATION_REPORT_SCHEMA_SHA256,
        report_length_limit_chars=5_000,
        reasoning_effort=reasoning_effort,
        provider_hostname=urlparse(settings.base_url).hostname,
        structured_output_mode=resolved_structured_output_mode(settings),
        streaming=streaming,
        request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        max_response_bytes=MAX_RESPONSE_BYTES,
        dataset_fingerprint_sha256=public["question_set_fingerprint_sha256"],
        question_input_sha256={
            question_id: item.input_sha256 for question_id, item in inputs.items()
        },
        budget=AblationBudget(
            budget_scope="shared",
            unknown_usage_policy="bounded_reserve",
            unknown_attempt_reserve_cny=0.375,
            max_verifier_calls=max_verifier_calls,
            max_report_calls=report_calls * 2,
            max_model_calls=max_verifier_calls + report_calls * 2,
            max_provider_api_calls=max_verifier_calls + report_calls * 2,
            max_input_tokens=(max_verifier_calls * verifier_input_upper)
            + (report_calls * 2 * report_input_upper),
            max_output_tokens=(max_verifier_calls * verifier_output)
            + (report_calls * 2 * report_output),
            max_reference_cost_cny=allocated_cost_cny,
            max_duration_seconds=7_200,
            max_context_characters=40_000,
        ),
    )


def simple_row_as_v_off(
    row: Any,
    *,
    manifest: AblationExecutionManifest,
    run_id: str,
) -> AblationPrivateRow:
    result = AblationResult(
        run_id=run_id,
        variant="V-off",
        question_id=row.question_id,
        split=row.split,
        status=row.status,
        frozen_input_sha256=row.frozen_input_sha256,
        evidence_identity_sha256=row.evidence_identity_sha256,
        prepared_context_sha256=row.prepared_context_sha256,
        configuration_sha256=manifest.configuration_sha256(),
        report_sha256=row.report_sha256,
        total_claim_count=row.total_claim_count,
        included_claim_count=row.included_claim_count,
        evidence_count=row.evidence_count,
        disposition_counts=row.disposition_counts,
        verifier_attempted_calls=0,
        verifier_calls=0,
        verifier_input_tokens=0,
        verifier_output_tokens=0,
        verifier_reference_cost_cny=0,
        verifier_reserved_reference_cost_cny=0,
        verifier_duration_seconds=0,
        unknown_attempts=0,
        unknown_reserved_reference_cost_cny=0,
        model_calls=row.model_calls,
        provider_api_calls=row.provider_api_calls,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        duration_seconds=row.duration_seconds,
        reference_cost_cny=row.reference_cost_cny,
        error_stage=row.error_stage,
        error_code=row.error_code,
        error_diagnostics=row.error_diagnostics,
    )
    return AblationPrivateRow(
        result=result,
        report=row.report,
        dispositions=row.dispositions,
        verifications=[],
        error_message=None,
    )


def make_counter(*, calls: int, output_tokens: int, max_cost_cny: float) -> ApiCallCounter:
    return ApiCallCounter(
        ApiCallBudget(
            max_calls=calls,
            max_input_token_upper_bound=30_000,
            max_output_tokens=output_tokens,
            max_cost_cny=max_cost_cny,
            reference_input_usd_per_million=LUNA_INPUT_USD_PER_MILLION,
            reference_output_usd_per_million=LUNA_OUTPUT_USD_PER_MILLION,
            reference_usd_to_cny=LUNA_USD_TO_CNY,
        )
    )


def allocated_costs(max_cost_cny: float) -> dict[str, float]:
    """Allocate one hard cap while accounting for the shared baseline rows."""

    allocations = {
        "baseline": max_cost_cny * BASELINE_COST_SHARE,
        "full_verification": max_cost_cny * FULL_COST_SHARE,
        "selective_verification": max_cost_cny * SELECTIVE_COST_SHARE,
    }
    # V-off baseline rows are copied into both ablation conditions, so the
    # conservative execution upper bound is F + S - B, not their raw sum.
    deduplicated_upper = (
        allocations["full_verification"]
        + allocations["selective_verification"]
        - allocations["baseline"]
    )
    if deduplicated_upper > max_cost_cny + 1e-9:
        raise ValueError("shared-baseline cost allocations exceed the hard cap")
    return allocations


def load_resumable_baseline(
    path: Path,
    *,
    expected: SimpleBaselineManifest,
) -> SimpleBaselineArchive:
    """Load a complete prior baseline without silently reusing another setup."""

    archive = SimpleBaselineArchive.model_validate_json(path.read_text(encoding="utf-8"))
    source = archive.manifest
    identity_fields = (
        "experiment_id",
        "execution_mode",
        "model_profile",
        "model_identifier",
        "provider_protocol",
        "report_prompt_sha256",
        "report_schema_sha256",
        "report_length_limit_chars",
        "automatic_retry",
        "reasoning_effort",
        "provider_hostname",
        "structured_output_mode",
        "streaming",
        "dataset_fingerprint_sha256",
        "question_input_sha256",
        "max_context_characters",
    )
    for field_name in identity_fields:
        if getattr(source, field_name) != getattr(expected, field_name):
            raise ValueError(f"baseline resume identity mismatch: {field_name}")
    if any(row.status not in {"succeeded", "degraded"} for row in archive.rows):
        raise ValueError(
            "baseline archive contains an incomplete row; refusing an unsafe automatic resend"
        )
    return archive


def load_resumable_ablation(
    path: Path,
    *,
    expected_manifest: Any,
    expected_run_id: str,
) -> tuple[list[AblationPrivateRow], list[AblationAttemptRecord], bool]:
    """Load rows and request history, allowing only an explicit budget migration."""

    source_payload = json.loads(path.read_text(encoding="utf-8"))
    migrated = source_payload.get("manifest_sha256") != expected_manifest.stable_sha256()
    recorder = FormalCheckpointRecorder.load(
        path,
        expected_manifest=expected_manifest,
        allow_budget_policy_migration=True,
    )
    if recorder.run_id != expected_run_id:
        raise ValueError("formal checkpoint run ID mismatch")
    return recorder.rows, recorder.attempts, migrated


def merge_existing_rows(
    *groups: list[AblationPrivateRow],
) -> list[AblationPrivateRow]:
    """Merge recovery rows by identity while retaining failed historical rows."""

    merged: dict[tuple[str, str], AblationPrivateRow] = {}
    for group in groups:
        for row in group:
            key = (row.result.question_id, row.result.variant)
            if key in merged and merged[key].result.status not in {"failed", "timeout"}:
                continue
            merged[key] = row
    return list(merged.values())


async def run_ablation_scheme(
    *,
    name: str,
    manifest: AblationExecutionManifest,
    inputs: Mapping[str, AblationFrozenInput],
    settings: Any,
    client: httpx.AsyncClient,
    existing_rows: list[AblationPrivateRow],
    existing_attempts: list[AblationAttemptRecord],
    selector: Any | None,
    run_id: str,
    private_dir: Path,
    public_path: Path,
    allocated_cost_cny: float,
    reasoning_effort: ReasoningEffort,
    compact_context: bool,
    retry_unknown: bool,
    streaming: bool,
) -> dict[str, Any]:
    verifier_calls = manifest.budget.max_verifier_calls
    verifier_counter = make_counter(
        calls=verifier_calls,
        output_tokens=2_000,
        max_cost_cny=allocated_cost_cny,
    )
    report_counter = make_counter(
        calls=len(inputs) * 2,
        output_tokens=4_000,
        max_cost_cny=allocated_cost_cny,
    )
    verifier = OpenAICompatibleSemanticVerifier(
        client=client,
        settings=settings,
        profile_id="api-strong",
        model=MODEL,
        protocol="responses",
        call_counter=verifier_counter,
        timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        reasoning_effort=reasoning_effort,
        streaming=streaming,
    )
    reporter_inner = OpenAICompatibleAblationReportGenerator(
        client=client,
        settings=settings,
        model_profile="api-strong",
        model=MODEL,
        protocol="responses",
        call_counter=report_counter,
        timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        reasoning_effort=reasoning_effort,
        max_output_tokens=REPORT_MAX_OUTPUT_TOKENS,
        streaming=streaming,
    )
    reporter = (
        CompactReportContextGenerator(reporter_inner)
        if compact_context
        else reporter_inner
    )
    private_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = private_dir / "formal_checkpoint.json"
    recorder = FormalCheckpointRecorder(
        path=checkpoint,
        manifest=manifest,
        run_id=run_id,
        attempts=existing_attempts,
        rows=existing_rows,
    )
    archive = await VerificationAblationRunner(
        report_generator=cast(AblationReportGenerator, reporter),
        verifier_backend=verifier,
        verifier_policy=enabled_policy(MODEL),
        allow_fixture=False,
        verification_selector=selector,
    ).run(
        manifest=manifest,
        inputs=inputs,
        run_id=run_id,
        existing_rows=existing_rows,
        existing_attempts=existing_attempts,
        on_attempt=recorder.record_attempt,
        on_checkpoint=recorder.record_row,
        variant_order=("V-on", "V-off"),
        retry_unknown=retry_unknown,
    )
    write_artifacts(
        archive=archive,
        private_path=private_dir / "formal_archive.json",
        public_path=public_path,
    )
    return {
        "scheme": name,
        "run_id": run_id,
        "passed": all(
            row.result.status in {"succeeded", "degraded"}
            for row in archive.rows
            if row.result.variant == "V-on"
        ),
        "execution_complete": all(
            row.result.status in {"succeeded", "degraded"}
            for row in archive.rows
            if row.result.variant == "V-on"
        ),
        "quality_gate_status": "pending_review",
        "adopted": False,
        "rows": len(archive.rows),
        "checkpoint": public_path_string(checkpoint),
        "public_output": public_path_string(public_path),
        "usage": {
            variant: usage.model_dump(mode="json")
            for variant, usage in archive.result_usage_by_variant.items()
        },
    }


def runtime_identity(
    *,
    settings: Any,
    provider_metadata: Mapping[str, Any],
    reasoning_effort: ReasoningEffort,
    compact_context: bool,
    streaming: bool,
) -> RuntimeConfigurationIdentity:
    """Build the runtime identity from the actual execution settings."""

    hostname = provider_metadata.get("provider_hostname")
    provider_id = provider_metadata.get("provider_id")
    if not isinstance(hostname, str) or not hostname:
        raise ValueError("runtime provider hostname is missing")
    if not isinstance(provider_id, str) or not provider_id:
        raise ValueError("runtime provider ID is missing")
    return RuntimeConfigurationIdentity(
        model_profile="api-strong",
        model_identifier=MODEL,
        provider_protocol="responses",
        provider_id=provider_id,
        provider_hostname=hostname,
        structured_output_mode=resolved_structured_output_mode(settings),
        reasoning_effort=reasoning_effort,
        streaming=streaming,
        compact_context=compact_context,
        context_transform_id=(COMPACT_CONTEXT_ID if compact_context else "none"),
        report_max_output_tokens=REPORT_MAX_OUTPUT_TOKENS,
        verifier_max_output_tokens=VERIFIER_MAX_OUTPUT_TOKENS,
        request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        max_response_bytes=MAX_RESPONSE_BYTES,
    )


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.approve_paid_calls and not args.preview:
        raise ValueError("--approve-paid-calls is required")
    if args.max_cost_cny <= 0 or args.max_cost_cny > 20:
        raise ValueError("max cost must be between 0 and 20 CNY")
    inputs = load_stage_two_inputs()
    settings, provider_metadata = load_ccswitch_codex_settings(
        provider_id=args.ccswitch_provider_id,
        model_override=MODEL,
        reasoning_effort=args.reasoning_effort,
    )
    reasoning_effort = cast(ReasoningEffort, args.reasoning_effort)
    compact_context = args.compact_context
    streaming = args.stream_responses
    if provider_metadata["provider_notes"] != "Luna":
        raise ValueError("selected cc-switch profile is not the current Luna provider")
    selected_count = sum(
        sum(should_verify(frozen, claim) for claim in frozen.claims)
        for frozen in inputs.values()
    )
    total_claims = sum(len(frozen.claims) for frozen in inputs.values())
    if selected_count != 5 or total_claims != 42:
        raise ValueError("SP-04 frozen question or selection counts drifted")
    allocations = allocated_costs(args.max_cost_cny)
    baseline_manifest = simple_manifest(inputs, settings, reasoning_effort, streaming)
    full_manifest = ablation_manifest(
        inputs=inputs,
        settings=settings,
        experiment_id="scholartrace-sp04-full-verification-production-v1",
        max_verifier_calls=total_claims,
        allocated_cost_cny=allocations["full_verification"],
        reasoning_effort=reasoning_effort,
        streaming=streaming,
    )
    candidate_manifest = ablation_manifest(
        inputs=inputs,
        settings=settings,
        experiment_id="scholartrace-sp04-selective-verification-production-v1",
        max_verifier_calls=selected_count,
        allocated_cost_cny=allocations["selective_verification"],
        reasoning_effort=reasoning_effort,
        streaming=streaming,
    )
    runtime_identity_value = runtime_identity(
        settings=settings,
        provider_metadata=provider_metadata,
        reasoning_effort=reasoning_effort,
        compact_context=compact_context,
        streaming=streaming,
    )
    runtime_configuration = runtime_configuration_payload(runtime_identity_value)
    if args.resume_dir:
        run_root = Path(args.resume_dir).expanduser().resolve()
        if run_root.parent != PRIVATE_ROOT.resolve():
            raise ValueError("--resume-dir must point to an SP-04 private run directory")
        if not run_root.is_dir():
            raise ValueError("--resume-dir does not exist")
        run_stamp = run_root.name
    else:
        run_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_root = PRIVATE_ROOT / run_stamp
    baseline_run_id = f"sp04-simple-baseline:{run_stamp}"
    full_run_id = f"sp04-full:{run_stamp}"
    candidate_run_id = f"sp04-selective:{run_stamp}"
    baseline_private = run_root / "simple-baseline"

    baseline_archive_path = baseline_private / "baseline_archive.json"
    full_checkpoint_path = run_root / "full-verification" / "formal_checkpoint.json"
    candidate_checkpoint_path = run_root / "selective-verification" / "formal_checkpoint.json"
    historical_snapshot_context: dict[str, Any] = {
        "available": False,
        "historical_unknown_count": 0,
        "note": "No pre-recovery snapshot is associated with this run.",
    }
    if run_stamp == "20260922T161810Z" and PRE_RECOVERY_SNAPSHOT.is_file():
        historical_snapshot = json.loads(
            PRE_RECOVERY_SNAPSHOT.read_text(encoding="utf-8")
        )
        historical_snapshot_context = {
            "available": True,
            "snapshot_path": public_path_string(PRE_RECOVERY_SNAPSHOT),
            "historical_unknown_count": len(historical_snapshot.get("unknown_sent", [])),
            "historical_success_count": int(
                historical_snapshot.get("attempt_states", {}).get("succeeded", 0)
            ),
            "historical_sent_count": int(historical_snapshot.get("sent_count", 0)),
            "note": (
                "The current checkpoint may show recovered success records; the immutable "
                "pre-recovery snapshot remains authoritative for historical unknown and "
                "replay counts."
            ),
        }

    if args.preview:
        baseline_preview: dict[str, Any] = {
            "exists": baseline_archive_path.is_file(),
            "compatible": False,
        }
        if baseline_archive_path.is_file():
            try:
                preview_archive = load_resumable_baseline(
                    baseline_archive_path,
                    expected=baseline_manifest,
                )
            except ValueError as exc:
                baseline_preview["error"] = str(exc)
            else:
                baseline_preview.update(
                    {
                        "compatible": True,
                        "reusable_rows": len(preview_archive.rows),
                        "statuses": dict(Counter(row.status for row in preview_archive.rows)),
                    }
                )

        def checkpoint_preview(
            path: Path,
            manifest: AblationExecutionManifest,
            run_id: str,
        ) -> dict[str, Any]:
            summary: dict[str, Any] = {"exists": path.is_file(), "compatible": False}
            if not path.is_file():
                return summary
            try:
                rows, attempts, migrated = load_resumable_ablation(
                    path,
                    expected_manifest=manifest,
                    expected_run_id=run_id,
                )
            except ValueError as exc:
                summary["error"] = str(exc)
                return summary
            reusable = [
                row for row in rows if row.result.status in {"succeeded", "degraded"}
            ]
            summary.update(
                {
                    "compatible": True,
                    "budget_policy_migration_required": migrated,
                    "row_statuses": dict(Counter(row.result.status for row in rows)),
                    "reusable_rows": len(reusable),
                    "pending_condition_rows": (len(inputs) * 2) - len(reusable),
                    "attempt_states": dict(Counter(item.state for item in attempts)),
                    "sent_unknown_attempts": sum(
                        item.attempted_calls
                        for item in attempts
                        if item.state in {"dispatched", "unknown"}
                    ),
                    "intent_only_attempts": sum(
                        item.state == "intent" for item in attempts
                    ),
                }
            )
            return summary

        preview = {
            "schema_version": "1.0",
            "purpose": "scholartrace-sp04-recovery-preview",
            "status": "preview_only",
            "provider_id": provider_metadata["provider_id"],
            "provider_hostname": urlparse(settings.base_url).hostname,
            "model": MODEL,
            "reasoning_effort": reasoning_effort,
            "compact_context": compact_context,
            "stream_responses": streaming,
            "runtime_configuration": runtime_configuration,
            "runtime_configuration_sha256": runtime_identity_value.fingerprint_sha256(),
            "hard_cap_cny": args.max_cost_cny,
            "allocations_cny": allocations,
            "deduplicated_upper_bound_cny": (
                allocations["full_verification"]
                + allocations["selective_verification"]
                - allocations["baseline"]
            ),
            "resume_dir": public_path_string(run_root),
            "historical_snapshot_context": historical_snapshot_context,
            "baseline": baseline_preview,
            "full_verification": checkpoint_preview(
                full_checkpoint_path,
                full_manifest,
                full_run_id,
            ),
            "selective_verification": checkpoint_preview(
                candidate_checkpoint_path,
                candidate_manifest,
                candidate_run_id,
            ),
            "network_requests_sent": 0,
        }
        return preview

    baseline_private.mkdir(parents=True, exist_ok=True)

    baseline_reused = False
    baseline_source_tree_sha256: str | None = None
    baseline_migration_note: str | None = None
    if baseline_archive_path.is_file() and args.resume_dir:
        baseline_archive = load_resumable_baseline(
            baseline_archive_path,
            expected=baseline_manifest,
        )
        baseline_reused = True
        baseline_source_tree_sha256 = baseline_archive.manifest.source_tree_sha256
        if baseline_source_tree_sha256 != baseline_manifest.source_tree_sha256:
            baseline_migration_note = (
                "baseline archive reused after source-tree change; original manifest retained"
            )
    elif args.resume_dir:
        raise ValueError("resume directory is missing its complete baseline archive")

    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        if not baseline_reused:
            baseline_counter = make_counter(
                calls=len(inputs),
                output_tokens=REPORT_MAX_OUTPUT_TOKENS,
                max_cost_cny=allocations["baseline"],
            )
            baseline_reporter_inner = OpenAICompatibleAblationReportGenerator(
                client=client,
                settings=settings,
                model_profile="api-strong",
                model=MODEL,
                protocol="responses",
                call_counter=baseline_counter,
                timeout_seconds=REQUEST_TIMEOUT_SECONDS,
                reasoning_effort=reasoning_effort,
                max_output_tokens=REPORT_MAX_OUTPUT_TOKENS,
                streaming=streaming,
            )
            baseline_reporter = (
                CompactReportContextGenerator(baseline_reporter_inner)
                if compact_context
                else baseline_reporter_inner
            )
            baseline_archive = await SimpleBaselineRunner(
                report_generator=baseline_reporter
            ).run(
                manifest=baseline_manifest,
                inputs=inputs,
                run_id=baseline_run_id,
            )
            write_simple_artifacts(
                archive=baseline_archive,
                private_path=baseline_archive_path,
                public_path=PUBLIC_ROOT / f"sp_04_simple_baseline_{run_stamp}.json",
            )

        baseline_rows_full = [
            simple_row_as_v_off(row, manifest=full_manifest, run_id=full_run_id)
            for row in baseline_archive.rows
        ]
        baseline_rows_candidate = [
            simple_row_as_v_off(row, manifest=candidate_manifest, run_id=candidate_run_id)
            for row in baseline_archive.rows
        ]

        full_existing = baseline_rows_full
        full_attempts: list[AblationAttemptRecord] = []
        full_migrated = False
        if full_checkpoint_path.is_file():
            checkpoint_rows, full_attempts, full_migrated = load_resumable_ablation(
                full_checkpoint_path,
                expected_manifest=full_manifest,
                expected_run_id=full_run_id,
            )
            full_existing = merge_existing_rows(baseline_rows_full, checkpoint_rows)

        candidate_existing = baseline_rows_candidate
        candidate_attempts: list[AblationAttemptRecord] = []
        candidate_migrated = False
        if candidate_checkpoint_path.is_file():
            checkpoint_rows, candidate_attempts, candidate_migrated = load_resumable_ablation(
                candidate_checkpoint_path,
                expected_manifest=candidate_manifest,
                expected_run_id=candidate_run_id,
            )
            candidate_existing = merge_existing_rows(
                baseline_rows_candidate,
                checkpoint_rows,
            )

        full_result = await run_ablation_scheme(
            name="full-verification",
            manifest=full_manifest,
            inputs=inputs,
            settings=settings,
            client=client,
            existing_rows=full_existing,
            existing_attempts=full_attempts,
            selector=None,
            run_id=full_run_id,
            private_dir=run_root / "full-verification",
            public_path=PUBLIC_ROOT / f"sp_04_full_verification_{run_stamp}.json",
            allocated_cost_cny=allocations["full_verification"],
            reasoning_effort=reasoning_effort,
            compact_context=compact_context,
            retry_unknown=args.retry_unknown,
            streaming=streaming,
        )
        candidate_result = await run_ablation_scheme(
            name="selective-verification",
            manifest=candidate_manifest,
            inputs=inputs,
            settings=settings,
            client=client,
            existing_rows=candidate_existing,
            existing_attempts=candidate_attempts,
            selector=should_verify,
            run_id=candidate_run_id,
            private_dir=run_root / "selective-verification",
            public_path=PUBLIC_ROOT / f"sp_04_selective_verification_{run_stamp}.json",
            allocated_cost_cny=allocations["selective_verification"],
            reasoning_effort=reasoning_effort,
            compact_context=compact_context,
            retry_unknown=args.retry_unknown,
            streaming=streaming,
        )
    execution_complete = bool(
        full_result["execution_complete"] and candidate_result["execution_complete"]
    )
    result = {
        "schema_version": "1.0",
        "purpose": "scholartrace-sp04-three-scheme-comparison",
        "status": "execution_completed" if execution_complete else "partial_or_failed",
        "execution_complete": execution_complete,
        "quality_gate_status": "pending_review",
        "adopted": False,
        "provider_source": provider_metadata["provider_source"],
        "provider_id": provider_metadata["provider_id"],
        "provider_hostname": urlparse(settings.base_url).hostname,
        "model": MODEL,
        "reasoning_effort": reasoning_effort,
        "compact_context": compact_context,
        "stream_responses": streaming,
        "runtime_configuration": runtime_configuration,
        "runtime_configuration_sha256": runtime_identity_value.fingerprint_sha256(),
        "question_count": len(inputs),
        "claim_count": total_claims,
        "selected_claim_count": selected_count,
        "reference_cost_cap_cny": args.max_cost_cny,
        "cost_allocations_cny": allocations,
        "deduplicated_upper_bound_cny": (
            allocations["full_verification"]
            + allocations["selective_verification"]
            - allocations["baseline"]
        ),
        "recovery": {
            "resume_dir": (
                public_path_string(run_root) if args.resume_dir else None
            ),
            "baseline_reused": baseline_reused,
            "baseline_source_tree_sha256": baseline_source_tree_sha256,
            "baseline_current_source_tree_sha256": baseline_manifest.source_tree_sha256,
            "baseline_migration_note": baseline_migration_note,
            "full_checkpoint_budget_migrated": full_migrated,
            "selective_checkpoint_budget_migrated": candidate_migrated,
            "retry_unknown": args.retry_unknown,
        },
        "baseline": {
            "run_id": baseline_run_id,
            "passed": all(row.status in {"succeeded", "degraded"} for row in baseline_archive.rows),
            "report_calls": baseline_archive.usage.report_calls,
            "model_calls": baseline_archive.usage.model_calls,
            "reference_cost_cny": baseline_archive.usage.reference_cost_cny,
        },
        "full_verification": full_result,
        "selective_verification": candidate_result,
        "human_review_required": True,
        "skipped_claim_audit_required": True,
    }
    write_json(PUBLIC_ROOT / "sp_04_comparison_summary.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approve-paid-calls", action="store_true")
    parser.add_argument("--max-cost-cny", type=float, default=10.0)
    parser.add_argument("--ccswitch-provider-id", default="sub2api-1789904127847")
    parser.add_argument("--reasoning-effort", choices=("high", "max"), default="high")
    parser.add_argument("--resume-dir", type=Path)
    parser.add_argument("--retry-unknown", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--stream-responses", action="store_true")
    context_group = parser.add_mutually_exclusive_group()
    context_group.add_argument(
        "--compact-context", dest="compact_context", action="store_true"
    )
    context_group.add_argument(
        "--no-compact-context", dest="compact_context", action="store_false"
    )
    parser.set_defaults(compact_context=True)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    if result.get("status") == "preview_only":
        preview_path = PUBLIC_ROOT / "sp_04_recovery_preview.json"
        result["output"] = public_path_string(preview_path)
        write_json(preview_path, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result.get("status") == "preview_only":
        return 0
    return 0 if all(
        item.get("passed", False)
        for item in (
            result["baseline"],
            result["full_verification"],
            result["selective_verification"],
        )
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
