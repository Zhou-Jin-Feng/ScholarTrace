"""Run the explicitly approved two-question SA-04 V-on/V-off pilot."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import tomllib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from scholartrace.contracts import ModelRoutingPolicy
from scholartrace.model_provider import (
    ApiCallBudget,
    ApiCallCounter,
    OpenAICompatibleCatalogClient,
    OpenAICompatibleSemanticVerifier,
)
from scholartrace.model_provider.settings import ProviderSettings, resolved_structured_output_mode
from scholartrace.search.storage import write_json
from scholartrace.verification_ablation import (
    ABLATION_REPORT_SCHEMA_SHA256,
    AblationAttemptRecord,
    AblationBudget,
    AblationExecutionManifest,
    AblationFrozenInput,
    AblationPrivateRow,
    AblationUsage,
    AblationVariant,
    OpenAICompatibleAblationReportGenerator,
    VerificationAblationRunner,
    load_frozen_inputs,
    write_artifacts,
)

ROOT = Path(__file__).resolve().parents[1]
FROZEN_INPUTS = ROOT / "agent" / "verification-ablation" / "SA-02" / "frozen_inputs.json"
SA02_MANIFEST = ROOT / "evaluation" / "seeds" / "sa_02_verification_ablation_manifest.json"
PRIVATE_DIR = ROOT / "agent" / "verification-ablation" / "SA-04"
PRIVATE_ARCHIVE = PRIVATE_DIR / "pilot_archive.json"
CHECKPOINT = PRIVATE_DIR / "pilot_checkpoint.json"
PUBLIC_OUTPUT = ROOT / "evaluation" / "reports" / "sa_04_verification_ablation_pilot.json"
PROVIDER_PREFLIGHT = ROOT / "evaluation" / "reports" / "sa_04_provider_preflight.json"
RETRY_HISTORY = PRIVATE_DIR / "pilot_retry_history.json"
PILOT_IDS = ("sa02-pilot-01", "sa02-pilot-02")
DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_MAX_COST_CNY = 8.0
CCSWITCH_DB = Path.home() / ".cc-switch" / "cc-switch.db"
CCSWITCH_GPT_PROVIDER_ID = "sub2api-1789904233520"


def _enabled_policy(model: str) -> ModelRoutingPolicy:
    policy_path = ROOT / "contracts" / "examples" / "m0_bundle.json"
    payload = json.loads(policy_path.read_text(encoding="utf-8"))["ModelRoutingPolicy"]
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
    return ModelRoutingPolicy.model_validate(payload)


def _load_ccswitch_gpt_settings(
    *,
    provider_id: str,
    model_override: str | None,
) -> tuple[ProviderSettings, dict[str, object]]:
    if not CCSWITCH_DB.is_file():
        raise ValueError(f"cc-switch database is missing: {CCSWITCH_DB}")
    connection = sqlite3.connect(CCSWITCH_DB)
    try:
        row = connection.execute(
            "select name, notes, website_url, settings_config from providers "
            "where id=? and app_type='codex'",
            (provider_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError("requested cc-switch Codex provider was not found")
    name, notes, website_url, raw_settings = row
    if notes not in {"GPT", "Luna"}:
        raise ValueError("selected cc-switch provider is not a GPT/Luna Codex profile")
    payload = json.loads(raw_settings)
    config = tomllib.loads(payload["config"])
    provider_config = config["model_providers"]["custom"]
    api_key = payload.get("auth", {}).get("OPENAI_API_KEY", "")
    model = model_override or config.get("model") or DEFAULT_MODEL
    settings = ProviderSettings(
        base_url=provider_config["base_url"],
        api_key=api_key,
        timeout_seconds=20,
        model=model,
        structured_output_mode="auto",
    )
    metadata = {
        "provider_id": provider_id,
        "provider_name": name,
        "provider_notes": notes,
        "provider_website": website_url,
        "base_hostname": urlparse(settings.base_url).hostname,
        "configured_model": config.get("model"),
        "selected_model": model,
        "api_key_present": bool(api_key.strip()),
        "reasoning_effort": "max",
    }
    return settings, metadata


def _manifest(
    inputs: Mapping[str, AblationFrozenInput],
    model: str,
    settings: ProviderSettings,
    max_cost: float,
) -> AblationExecutionManifest:
    public_manifest = json.loads(SA02_MANIFEST.read_text(encoding="utf-8"))
    provider_settings = settings
    return AblationExecutionManifest(
        experiment_id="scholartrace-sa04-verification-ablation-pilot-v1",
        execution_mode="production",
        baseline_commit="3d095a79feff257b57b041de40b7b5ed73021f9e",
        model_profile="api-strong",
        model_identifier=model,
        provider_protocol="responses",
        verifier_prompt_sha256=OpenAICompatibleSemanticVerifier.prompt_template_sha256,
        report_prompt_sha256=OpenAICompatibleAblationReportGenerator.prompt_template_sha256,
        report_schema_sha256=ABLATION_REPORT_SCHEMA_SHA256,
        report_length_limit_chars=5_000,
        dataset_fingerprint_sha256=public_manifest["dataset_fingerprint_sha256"],
        question_input_sha256={
            question_id: frozen.input_sha256
            for question_id, frozen in inputs.items()
        },
        provider_hostname=urlparse(provider_settings.base_url).hostname,
        structured_output_mode=resolved_structured_output_mode(provider_settings),
        request_timeout_seconds=180,
        max_response_bytes=1_000_000,
        reasoning_effort="max",
        budget=AblationBudget(
            budget_scope="shared",
            max_verifier_calls=12,
            max_report_calls=4,
            max_model_calls=16,
            max_provider_api_calls=16,
            max_input_tokens=200_000,
        max_output_tokens=40_000,
            max_reference_cost_cny=max_cost,
            max_duration_seconds=3_600,
            max_context_characters=40_000,
        ),
    )


async def _catalog(settings: ProviderSettings) -> list[dict[str, object]]:
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        catalog = OpenAICompatibleCatalogClient(
            client=client,
            base_url=settings.base_url,
            timeout_seconds=settings.timeout_seconds,
        )
        models = await catalog.list_models(settings.api_key)
    return [
        {
            "model_id": item.model_id,
            "owned_by": item.owned_by,
            "created": item.created,
            "context_window": item.context_window,
        }
        for item in models
    ]


def _write_checkpoint(
    *,
    manifest: AblationExecutionManifest,
    run_id: str,
    row: AblationPrivateRow,
    usages: Mapping[AblationVariant, AblationUsage],
) -> None:
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    existing: dict[str, object] = {}
    if CHECKPOINT.is_file():
        existing = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    raw_rows = existing.get("rows", [])
    rows = list(raw_rows) if isinstance(raw_rows, list) else []
    key = (row.result.question_id, row.result.variant)
    rows = [
        item
        for item in rows
        if (item["result"]["question_id"], item["result"]["variant"]) != key
    ]
    rows.append(row.model_dump(mode="json"))
    attempts = existing.get("attempts", [])
    if not isinstance(attempts, list):
        attempts = []
    write_json(
        CHECKPOINT,
        {
            "schema_version": "1.0",
            "purpose": "scholartrace-sa04-private-pilot-checkpoint",
            "run_id": run_id,
            "manifest": manifest.model_dump(mode="json"),
            "manifest_sha256": manifest.stable_sha256(),
            "usage_by_variant": {
                variant: usage.model_dump(mode="json")
                for variant, usage in usages.items()
            },
            "attempts": attempts,
            "rows": rows,
        },
    )


def _write_attempt(attempt: AblationAttemptRecord) -> None:
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    existing: dict[str, object] = {}
    if CHECKPOINT.is_file():
        existing = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    raw_attempts = existing.get("attempts", [])
    attempts = list(raw_attempts) if isinstance(raw_attempts, list) else []
    attempts = [
        item for item in attempts if item.get("attempt_id") != attempt.attempt_id
    ]
    attempts.append(attempt.model_dump(mode="json"))
    existing["attempts"] = attempts
    write_json(CHECKPOINT, existing)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.approve_paid_calls:
        raise ValueError("SA-04 pilot requires --approve-paid-calls")
    if args.max_cost_cny <= 0 or args.max_cost_cny > 8:
        raise ValueError("SA-04 max cost must be between 0 and 8 CNY")
    if args.retry_failed and not args.resume:
        raise ValueError("--retry-failed requires --resume")
    if PRIVATE_ARCHIVE.exists() and not args.resume:
        raise ValueError("SA-04 private archive already exists; use --resume explicitly")
    inputs_all = load_frozen_inputs(FROZEN_INPUTS)
    inputs = {question_id: inputs_all[question_id] for question_id in PILOT_IDS}
    public_manifest = json.loads(SA02_MANIFEST.read_text(encoding="utf-8"))
    settings, provider_metadata = _load_ccswitch_gpt_settings(
        provider_id=args.ccswitch_provider_id,
        model_override=args.model or DEFAULT_MODEL,
    )
    if not settings.api_key.strip():
        raise ValueError("Provider API key is missing; no request was made")
    selected_model = settings.model or DEFAULT_MODEL
    models = await _catalog(settings)
    model_ids = {str(item["model_id"]) for item in models}
    if selected_model not in model_ids:
        raise ValueError(f"selected model is not visible in the current catalog: {selected_model}")
    if (
        not args.allow_prompt_revision
        and OpenAICompatibleAblationReportGenerator.prompt_template_sha256
        != public_manifest["experiment_contract"]["report_prompt_sha256"]
    ):
        raise ValueError("SA-02 report Prompt hash does not match the adapter")
    provider_preflight = {
        "schema_version": "1.0",
        "purpose": "scholartrace-sa04-provider-preflight",
        "status": "passed",
        "generated_at": datetime.now(UTC).isoformat(),
        "provider_hostname": urlparse(settings.base_url).hostname,
        "provider_source": "cc-switch",
        "provider_id": provider_metadata["provider_id"],
        "provider_name": provider_metadata["provider_name"],
        "provider_notes": provider_metadata["provider_notes"],
        "selected_model": selected_model,
        "catalog_models": models,
        "protocol": "responses",
        "structured_output_mode": resolved_structured_output_mode(settings),
        "reasoning_effort": "max",
        "prompt_revision": (
            "sa04-answer-status-findings-v2"
            if args.allow_prompt_revision
            else "sa02-frozen"
        ),
        "api_key_stored": False,
        "inference_calls_made": 0,
        "billing_observability": "unknown_until_inference_response",
    }
    write_json(PROVIDER_PREFLIGHT, provider_preflight)
    manifest = _manifest(inputs, selected_model, settings, args.max_cost_cny)
    verifier_counter = ApiCallCounter(
        ApiCallBudget(
            max_calls=12,
            max_input_token_upper_bound=10_000,
            max_output_tokens=2_000,
            max_cost_cny=min(6.0, args.max_cost_cny),
        )
    )
    report_counter = ApiCallCounter(
        ApiCallBudget(
            max_calls=4,
            max_input_token_upper_bound=20_000,
            max_output_tokens=4_000,
            max_cost_cny=args.max_cost_cny,
        )
    )
    run_id = f"run:sa04:pilot:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    existing_rows: list[AblationPrivateRow] = []
    existing_attempts: list[AblationAttemptRecord] = []
    retried_failure: dict[str, object] | None = None
    if args.resume and CHECKPOINT.is_file():
        checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
        if checkpoint.get("manifest_sha256") != manifest.stable_sha256():
            raise ValueError("checkpoint manifest drifted; refusing resume")
        run_id = str(checkpoint["run_id"])
        existing_rows = [AblationPrivateRow.model_validate(item) for item in checkpoint["rows"]]
        existing_attempts = [
            AblationAttemptRecord.model_validate(item)
            for item in checkpoint.get("attempts", [])
        ]
        if args.retry_failed:
            failed = [row for row in existing_rows if row.result.status == "failed"]
            expected_key = ("sa02-pilot-01", "V-off")
            if len(failed) != 1 or (
                failed[0].result.question_id,
                failed[0].result.variant,
            ) != expected_key:
                raise ValueError(
                    "retry requires exactly one failed sa02-pilot-01 V-off row"
                )
            retried_failure = {
                "question_id": failed[0].result.question_id,
                "variant": failed[0].result.variant,
                "status": failed[0].result.status,
                "error_stage": failed[0].result.error_stage,
                "error_code": failed[0].result.error_code,
                "retry_number": 1,
            }
            existing_rows = [row for row in existing_rows if row not in failed]
            write_json(
                RETRY_HISTORY,
                {
                    "schema_version": "1.0",
                    "purpose": "scholartrace-sa04-private-pilot-retry-history",
                    "run_id": run_id,
                    "entries": [retried_failure],
                },
            )
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        verifier = OpenAICompatibleSemanticVerifier(
            client=client,
            settings=settings,
            profile_id="api-strong",
            model=selected_model,
            protocol="responses",
            call_counter=verifier_counter,
            timeout_seconds=180,
            reasoning_effort="max",
        )
        reporter = OpenAICompatibleAblationReportGenerator(
            client=client,
            settings=settings,
            model_profile="api-strong",
            model=selected_model,
            protocol="responses",
            call_counter=report_counter,
            timeout_seconds=180,
            reasoning_effort="max",
            max_output_tokens=4_000,
        )
        archive = await VerificationAblationRunner(
            report_generator=reporter,
            verifier_backend=verifier,
            verifier_policy=_enabled_policy(selected_model),
            allow_fixture=False,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id=run_id,
            existing_rows=existing_rows,
            existing_attempts=existing_attempts,
            on_attempt=_write_attempt,
            on_checkpoint=lambda row, usages: _write_checkpoint(
                manifest=manifest,
                run_id=run_id,
                row=row,
                usages=usages,
            ),
            variant_order=("V-off", "V-on"),
        )
    write_artifacts(
        archive=archive,
        private_path=PRIVATE_ARCHIVE,
        public_path=PUBLIC_OUTPUT,
    )
    public: dict[str, Any] = json.loads(PUBLIC_OUTPUT.read_text(encoding="utf-8"))
    report_cost = sum(
        (usage.reference_cost_cny or 0)
        for usage in archive.usage_by_variant.values()
    )
    verifier_cost = sum(
        (usage.verifier_reference_cost_cny or 0)
        for usage in archive.usage_by_variant.values()
    )
    reserved_cost = sum(
        (usage.verifier_reserved_reference_cost_cny or 0)
        for usage in archive.usage_by_variant.values()
    )
    statuses = [row.result.status for row in archive.rows]
    public.update(
        {
            "pilot_kind": "sa04_real_v_on_v_off_two_question_pilot",
            "quality_scope": (
                "Protocol, semantic-verification, failure, usage, cost, and traceability pilot; "
                "not a formal quality conclusion."
            ),
            "selected_model": selected_model,
            "catalog_model_count": len(models),
            "approved_reference_cap_cny": args.max_cost_cny,
            "provider_billed_cost_cny": None,
            "provider_billing_observability": "unavailable_in_response",
            "report_reference_cost_cny": round(report_cost, 6),
            "verifier_reference_cost_cny": round(verifier_cost, 6),
            "verifier_reserved_reference_cost_cny": round(reserved_cost, 6),
            "total_reference_cost_cny": round(report_cost + verifier_cost, 6),
            "total_reference_cost_upper_bound_cny": round(
                report_cost + verifier_cost + reserved_cost,
                6,
            ),
            "retry_history": retried_failure,
            "passed": all(status in {"succeeded", "degraded"} for status in statuses)
            and (report_cost + verifier_cost + reserved_cost) <= args.max_cost_cny,
            "raw_prompt_stored_publicly": False,
            "raw_response_stored_publicly": False,
        }
    )
    write_json(PUBLIC_OUTPUT, public)
    return public


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approve-paid-calls", action="store_true")
    parser.add_argument("--max-cost-cny", type=float, default=DEFAULT_MAX_COST_CNY)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--ccswitch-provider-id",
        default=CCSWITCH_GPT_PROVIDER_ID,
        help="cc-switch Codex provider ID; defaults to the notes=GPT SuperBoy profile.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--allow-prompt-revision", action="store_true")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
    except Exception as exc:  # noqa: BLE001 - public failure must be redacted
        failure = {
            "schema_version": "1.0",
            "purpose": "scholartrace-sa04-real-pilot-failure",
            "status": "failed",
            "error_code": type(exc).__name__,
            "error_message": str(exc),
            "raw_response_stored_publicly": False,
            "api_key_stored_publicly": False,
        }
        write_json(PUBLIC_OUTPUT, failure)
        print(json.dumps(failure, ensure_ascii=False, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "status": "completed",
                "passed": result["passed"],
                "selected_model": result["selected_model"],
                "total_reference_cost_cny": result["total_reference_cost_cny"],
                "total_reference_cost_upper_bound_cny": result[
                    "total_reference_cost_upper_bound_cny"
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
