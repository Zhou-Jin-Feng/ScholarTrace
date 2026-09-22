"""Run the frozen SA-05 formal V-on/V-off comparison on the current Luna provider."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import random
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from scholartrace.contracts import ModelRoutingPolicy
from scholartrace.model_provider import (
    ApiCallBudget,
    ApiCallCounter,
    OpenAICompatibleSemanticVerifier,
)
from scholartrace.model_provider.settings import (
    ProviderSettings,
    read_dotenv,
    resolve_provider_settings,
    resolved_structured_output_mode,
)
from scholartrace.search.storage import write_json
from scholartrace.verification_ablation import (
    ABLATION_REPORT_SCHEMA_SHA256,
    AblationAttemptRecord,
    AblationBudget,
    AblationExecutionManifest,
    AblationFrozenInput,
    AblationPrivateArchive,
    AblationPrivateRow,
    AblationUsage,
    FormalCheckpointRecorder,
    OpenAICompatibleAblationReportGenerator,
    VerificationAblationRunner,
    load_frozen_inputs,
    write_artifacts,
)

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "agent" / "verification-ablation" / "SA-02" / "frozen_inputs.json"
SA02_MANIFEST = ROOT / "evaluation" / "seeds" / "sa_02_verification_ablation_manifest.json"
PRIVATE_DIR = ROOT / "agent" / "verification-ablation" / "SA-05-formal"
PRIVATE_ARCHIVE = PRIVATE_DIR / "formal_archive.json"
CHECKPOINT = PRIVATE_DIR / "formal_checkpoint.json"
PUBLIC_OUTPUT = ROOT / "evaluation" / "reports" / "sa_05_formal_v_on_v_off.json"
REVIEW = PRIVATE_DIR / "blind_review.csv"
MAPPING = PRIVATE_DIR / "blind_mapping.json"
RETRY_HISTORY = PRIVATE_DIR / "formal_retry_history.json"
MODEL = "gpt-5.6-luna"


def enabled_policy(model: str) -> ModelRoutingPolicy:
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
    return ModelRoutingPolicy.model_validate(payload)


def load_luna_settings() -> ProviderSettings:
    settings = resolve_provider_settings(
        cli_values={"model": MODEL},
        environment=os.environ,
        dotenv_values=read_dotenv(ROOT / ".env"),
    )
    if not settings.api_key.strip():
        raise ValueError("SCHOLARTRACE_API_KEY is required for an explicit formal run")
    return settings


def formal_manifest(
    inputs: Mapping[str, AblationFrozenInput],
    settings: ProviderSettings,
) -> AblationExecutionManifest:
    public = json.loads(SA02_MANIFEST.read_text(encoding="utf-8"))
    return AblationExecutionManifest(
        experiment_id="scholartrace-sa05-formal-v-on-v-off-v1",
        execution_mode="production",
        baseline_commit="3d095a79feff257b57b041de40b7b5ed73021f9e",
        model_profile="api-strong",
        model_identifier=MODEL,
        provider_protocol="responses",
        verifier_prompt_sha256=OpenAICompatibleSemanticVerifier.prompt_template_sha256,
        report_prompt_sha256=OpenAICompatibleAblationReportGenerator.prompt_template_sha256,
        report_schema_sha256=ABLATION_REPORT_SCHEMA_SHA256,
        report_length_limit_chars=5_000,
        automatic_retry=False,
        reasoning_effort="max",
        dataset_fingerprint_sha256=public["dataset_fingerprint_sha256"],
        question_input_sha256={qid: item.input_sha256 for qid, item in inputs.items()},
        provider_hostname=settings.base_url.split("/")[2],
        structured_output_mode=resolved_structured_output_mode(settings),
        request_timeout_seconds=180,
        max_response_bytes=1_000_000,
        budget=AblationBudget(
            budget_scope="shared",
            unknown_usage_policy="bounded_reserve",
            unknown_attempt_reserve_cny=0.375,
            max_verifier_calls=56,
            max_report_calls=20,
            max_model_calls=76,
            max_provider_api_calls=76,
            max_input_tokens=960_000,
            max_output_tokens=60_000,
            max_reference_cost_cny=24,
            max_duration_seconds=86_400,
            max_context_characters=40_000,
        ),
    )


def checkpoint_writer(
    row: AblationPrivateRow,
    usages: Mapping[str, AblationUsage],
    attempts: list[AblationAttemptRecord],
    manifest: AblationExecutionManifest,
    run_id: str,
) -> None:
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if CHECKPOINT.is_file():
        existing = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    rows = list(existing.get("rows", []))
    key = (row.result.question_id, row.result.variant)
    rows = [
        item for item in rows if (item["result"]["question_id"], item["result"]["variant"]) != key
    ]
    rows.append(row.model_dump(mode="json"))
    write_json(
        CHECKPOINT,
        {
            "schema_version": "1.0",
            "purpose": "scholartrace-sa05-private-formal-checkpoint",
            "run_id": run_id,
            "manifest": manifest.model_dump(mode="json"),
            "manifest_sha256": manifest.stable_sha256(),
            "usage_by_variant": {
                variant: usage.model_dump(mode="json") for variant, usage in usages.items()
            },
            "attempts": [item.model_dump(mode="json") for item in attempts],
            "rows": rows,
        },
    )


def build_blind_review(archive: AblationPrivateArchive) -> dict[str, Any]:
    rng = random.Random(20260921)
    rows: list[dict[str, str]] = []
    mapping: list[dict[str, str]] = []
    for index, question_id in enumerate(sorted(archive.manifest.question_input_sha256), 1):
        by_variant = {
            row.result.variant: row for row in archive.rows if row.result.question_id == question_id
        }
        swap = bool(rng.randrange(2))
        a_variant: Literal["V-on", "V-off"]
        b_variant: Literal["V-on", "V-off"]
        a_variant, b_variant = ("V-off", "V-on") if swap else ("V-on", "V-off")
        blind_id = f"blind:sa05:{index:02d}"
        rows.append(
            {
                "blind_id": blind_id,
                "question_id": question_id,
                "answer_a": by_variant[a_variant].report,
                "answer_b": by_variant[b_variant].report,
                "score_a": "",
                "score_b": "",
            }
        )
        mapping.append(
            {
                "blind_id": blind_id,
                "question_id": question_id,
                "label_a_variant": a_variant,
                "label_b_variant": b_variant,
                "answer_a_sha256": by_variant[a_variant].result.report_sha256,
                "answer_b_sha256": by_variant[b_variant].result.report_sha256,
            }
        )
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    with REVIEW.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["blind_id", "question_id", "answer_a", "answer_b", "score_a", "score_b"],
        )
        writer.writeheader()
        writer.writerows(rows)
    write_json(
        MAPPING,
        {
            "schema_version": "1.0",
            "purpose": "scholartrace-sa05-private-blind-mapping",
            "entries": mapping,
        },
    )
    return {
        "row_count": len(rows),
        "review_sha256": hashlib.sha256(REVIEW.read_bytes()).hexdigest(),
        "mapping_sha256": hashlib.sha256(MAPPING.read_bytes()).hexdigest(),
        "score_scale": {"minimum": 0, "maximum": 4, "integer_only": True},
        "mapping_disclosed": False,
    }


async def run(*, resume: bool = False, retry_unknown: bool = False) -> dict[str, Any]:
    if retry_unknown and not resume:
        raise ValueError("retry_unknown requires resume")
    if resume and not CHECKPOINT.is_file():
        raise ValueError("resume requires an existing checkpoint")
    if not resume and (CHECKPOINT.exists() or PRIVATE_ARCHIVE.exists()):
        raise ValueError("existing formal artifacts require explicit resume; never overwrite a run")
    all_inputs = load_frozen_inputs(INPUTS)
    inputs = {qid: item for qid, item in all_inputs.items() if item.split == "formal"}
    if len(inputs) != 10:
        raise ValueError(f"expected 10 formal questions, got {len(inputs)}")
    settings = load_luna_settings()
    manifest = formal_manifest(inputs, settings)
    verifier_counter = ApiCallCounter(
        ApiCallBudget(
            max_calls=56,
            max_input_token_upper_bound=10_000,
            max_output_tokens=2_000,
            max_cost_cny=24,
        )
    )
    report_counter = ApiCallCounter(
        ApiCallBudget(
            max_calls=20,
            max_input_token_upper_bound=20_000,
            max_output_tokens=4_000,
            max_cost_cny=24,
        )
    )
    verifier = OpenAICompatibleSemanticVerifier(
        client=(client := httpx.AsyncClient(trust_env=False, follow_redirects=False)),
        settings=settings,
        profile_id="api-strong",
        model=MODEL,
        protocol="responses",
        call_counter=verifier_counter,
        timeout_seconds=180,
        reasoning_effort="max",
    )
    reporter = OpenAICompatibleAblationReportGenerator(
        client=client,
        settings=settings,
        model_profile="api-strong",
        model=MODEL,
        protocol="responses",
        call_counter=report_counter,
        timeout_seconds=180,
        reasoning_effort="max",
        max_output_tokens=4_000,
    )
    run_id = f"run:sa05:formal:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    recorder: FormalCheckpointRecorder
    existing_rows: list[AblationPrivateRow] = []
    existing_attempts: list[AblationAttemptRecord] = []
    if resume and CHECKPOINT.is_file():
        recorder = FormalCheckpointRecorder.load(
            CHECKPOINT,
            expected_manifest=manifest,
            allow_budget_policy_migration=True,
        )
        if recorder.manifest_sha256 != manifest.stable_sha256():
            raise ValueError("formal checkpoint manifest drifted")
        run_id = recorder.run_id
        existing_rows = recorder.rows
        existing_attempts = recorder.attempts
        if retry_unknown:
            failed = [
                row
                for row in existing_rows
                if row.result.status == "failed"
                and (
                    row.result.model_calls is None
                    or row.result.error_code in {"budget_exceeded", "ProviderBudgetError"}
                )
            ]
            if not failed:
                raise ValueError("retry_unknown requested but no unknown formal rows exist")
            if not (PRIVATE_DIR / "formal_checkpoint_attempt1.json").exists():
                shutil.copy2(
                    CHECKPOINT,
                    PRIVATE_DIR / "formal_checkpoint_attempt1.json",
                )
            retry_keys = {(row.result.question_id, row.result.variant) for row in failed}
            existing_rows = [
                row
                for row in existing_rows
                if (row.result.question_id, row.result.variant) not in retry_keys
            ]
            retry_payload: dict[str, Any] = {"rounds": []}
            if RETRY_HISTORY.is_file():
                retry_payload = json.loads(RETRY_HISTORY.read_text(encoding="utf-8"))
            rounds = list(retry_payload.get("rounds", []))
            retry_number = len(rounds) + 1
            rounds.append(
                {
                    "retry_number": retry_number,
                    "recorded_at": datetime.now(UTC).isoformat(),
                    "unknown_resend_explicitly_authorized": True,
                    "rows_selected_for_retry": [
                        {
                            "question_id": row.result.question_id,
                            "variant": row.result.variant,
                            "error_code": row.result.error_code,
                        }
                        for row in failed
                    ],
                }
            )
            write_json(
                RETRY_HISTORY,
                {
                    "schema_version": "1.0",
                    "purpose": "scholartrace-sa05-formal-retry-history",
                    "rounds": rounds,
                },
            )
    else:
        recorder = FormalCheckpointRecorder(
            path=CHECKPOINT,
            manifest=manifest,
            run_id=run_id,
        )
    try:
        archive = await VerificationAblationRunner(
            report_generator=reporter,
            verifier_backend=verifier,
            verifier_policy=enabled_policy(MODEL),
            allow_fixture=False,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id=run_id,
            existing_rows=existing_rows,
            existing_attempts=existing_attempts,
            variant_order=("V-off", "V-on"),
            on_attempt=recorder.record_attempt,
            on_checkpoint=recorder.record_row,
            retry_unknown=retry_unknown,
        )
    finally:
        await client.aclose()
    write_artifacts(
        archive=archive,
        private_path=PRIVATE_ARCHIVE,
        public_path=PUBLIC_OUTPUT,
    )
    review = build_blind_review(archive)
    public: dict[str, Any] = json.loads(PUBLIC_OUTPUT.read_text(encoding="utf-8"))
    public.update(
        {
            "purpose": "scholartrace-sa05-formal-v-on-v-off",
            "passed": all(row.result.status in {"succeeded", "degraded"} for row in archive.rows),
            "quality_scope": (
                "Formal raw reports and blind-review package; no human quality scores yet."
            ),
            "selected_model": MODEL,
            "reasoning_effort": "max",
            "formal_question_count": len(inputs),
            "blind_review": review,
            "formal_quality_scores_present": False,
        }
    )
    write_json(PUBLIC_OUTPUT, public)
    return public


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approve-paid-calls", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-unknown", action="store_true")
    args = parser.parse_args()
    if not args.approve_paid_calls:
        parser.error("--approve-paid-calls is required; this command can incur provider charges")
    result = asyncio.run(run(resume=args.resume, retry_unknown=args.retry_unknown))
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "formal_question_count": result["formal_question_count"],
                "blind_review_rows": result["blind_review"]["row_count"],
                "quality_scores_present": result["formal_quality_scores_present"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
