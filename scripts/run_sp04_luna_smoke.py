"""Run one bounded Luna report smoke before the stage-two comparison."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

import httpx

from scholartrace.model_provider import (
    ApiCallBudget,
    ApiCallCounter,
    load_ccswitch_codex_settings,
)
from scholartrace.search.storage import write_json
from scholartrace.verification_ablation import CompactReportContextGenerator
from scholartrace.verification_ablation.models import (
    AblationClaimDisposition,
    AblationFrozenInput,
    AblationReportRequest,
    canonical_sha256,
)
from scholartrace.verification_ablation.provider import OpenAICompatibleAblationReportGenerator
from scholartrace.verification_ablation.runner import prepare_variant_input

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_INPUTS = ROOT / "agent" / "verification-ablation" / "SP-02" / "frozen_inputs.json"
OUTPUT = ROOT / "evaluation" / "reports" / "sp_04_luna_smoke.json"
MODEL = "gpt-5.6-luna"


def load_input(question_id: str) -> AblationFrozenInput:
    payload = json.loads(PRIVATE_INPUTS.read_text(encoding="utf-8"))
    item = next(
        item["input"]
        for item in payload["questions"]
        if item["input"]["question_id"] == question_id
    )
    return AblationFrozenInput.model_validate(item)


async def run(
    question_id: str,
    protocol: Literal["responses", "chat_completions"],
    max_output_tokens: int,
    structured_output_mode: Literal["auto", "json_schema", "json_object"],
    compact_context: bool,
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"],
    stream_responses: bool,
) -> dict[str, object]:
    settings, provider_metadata = load_ccswitch_codex_settings(
        model_override=MODEL,
        reasoning_effort=reasoning_effort,
    )
    if settings.model != MODEL:
        raise ValueError(f"configured model mismatch: {settings.model}")
    settings = replace(settings, structured_output_mode=structured_output_mode)
    if not settings.api_key.strip():
        raise ValueError("provider key is not configured")
    frozen = load_input(question_id)
    dispositions = tuple(
        AblationClaimDisposition(
            claim_id=claim.claim_id,
            status="unverified",
            included=True,
            marker="[UNVERIFIED]",
        )
        for claim in sorted(frozen.claims, key=lambda item: item.claim_id)
    )
    prepared = prepare_variant_input(
        frozen=frozen,
        variant="V-off",
        dispositions=dispositions,
        max_context_characters=40_000,
    )
    configuration_sha256 = canonical_sha256(
        {
            "experiment": "sp04-luna-smoke",
            "model": MODEL,
            "protocol": "responses",
            "reasoning_effort": reasoning_effort,
        }
    )
    request = AblationReportRequest(
        question_id=frozen.question_id,
        split=frozen.split,
        question=frozen.question,
        variant="V-off",
        frozen_input_sha256=frozen.input_sha256,
        evidence_identity_sha256=prepared.evidence_identity_sha256,
        configuration_sha256=configuration_sha256,
        report_length_limit_chars=5_000,
        prepared_context=prepared.prepared_context,
        allowed_evidence_ids=prepared.allowed_evidence_ids,
        dispositions=prepared.dispositions,
    )
    counter = ApiCallCounter(
        ApiCallBudget(
            max_calls=1,
            max_input_token_upper_bound=20_000,
            max_output_tokens=max_output_tokens,
            max_cost_cny=5.0,
            reference_input_usd_per_million=0.2,
            reference_output_usd_per_million=1.2,
        )
    )
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        reporter_inner = OpenAICompatibleAblationReportGenerator(
            client=client,
            settings=settings,
            model_profile="api-strong",
            model=MODEL,
            protocol=protocol,
            call_counter=counter,
            timeout_seconds=180,
            reasoning_effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
            streaming=stream_responses,
        )
        reporter = (
            CompactReportContextGenerator(reporter_inner)
            if compact_context
            else reporter_inner
        )
        generated = await reporter.generate(request)
    host = urlsplit(settings.base_url).netloc
    summary = {
        "schema_version": "1.0",
        "purpose": "scholartrace-sp04-luna-direct-report-smoke",
        "passed": generated.status in {"succeeded", "degraded"},
        "provider_source": provider_metadata["provider_source"],
        "provider_id": provider_metadata["provider_id"],
        "provider_host": host,
        "model": MODEL,
        "protocol": protocol,
        "reasoning_effort": reasoning_effort,
        "max_output_tokens": max_output_tokens,
        "structured_output_mode": structured_output_mode,
        "compact_context": compact_context,
        "stream_responses": stream_responses,
        "question_id": frozen.question_id,
        "status": generated.status,
        "model_calls": generated.model_calls,
        "provider_api_calls": generated.provider_api_calls,
        "input_tokens": generated.input_tokens,
        "output_tokens": generated.output_tokens,
        "reference_cost_cny": generated.reference_cost_cny,
        "attempted_calls": counter.attempted_calls,
        "report_sha256": hashlib.sha256(generated.report.encode("utf-8")).hexdigest(),
        "raw_report_stored": False,
        "provider_billed_cost_cny": None,
        "notes": [
            "Direct Responses smoke with the explicitly authorized Luna max configuration.",
            "Provider billing and multiplier remain unavailable unless returned by the response.",
            "A passing smoke establishes bounded protocol compatibility only, not quality.",
        ],
    }
    write_json(OUTPUT, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--question-id", default="sp02-new-01")
    parser.add_argument("--max-output-tokens", type=int, default=2_000)
    parser.add_argument(
        "--structured-output-mode",
        choices=("auto", "json_schema", "json_object"),
        default="auto",
    )
    parser.add_argument("--compact-context", action="store_true")
    parser.add_argument("--stream-responses", action="store_true")
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"),
        default="max",
    )
    parser.add_argument(
        "--protocol", choices=("responses", "chat_completions"), default="responses"
    )
    args = parser.parse_args()
    try:
        protocol = cast(Literal["responses", "chat_completions"], args.protocol)
        structured_mode = cast(
            Literal["auto", "json_schema", "json_object"],
            args.structured_output_mode,
        )
        reasoning_effort = cast(
            Literal["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"],
            args.reasoning_effort,
        )
        summary = asyncio.run(
            run(
                args.question_id,
                protocol,
                args.max_output_tokens,
                structured_mode,
                args.compact_context,
                reasoning_effort,
                args.stream_responses,
            )
        )
    except Exception as exc:  # noqa: BLE001 - emit sanitized smoke status
        raw_diagnostics = getattr(exc, "diagnostics", None)
        allowed_keys = {
            "category",
            "status_code",
            "response_bytes",
            "response_sha256",
            "error_paths",
            "output_item_count",
            "response_status",
            "usage_present",
        }
        diagnostics = (
            {
                key: value
                for key, value in raw_diagnostics.items()
                if key in allowed_keys
                and (
                    isinstance(value, (str, int, float, bool))
                    or value is None
                    or (
                        isinstance(value, list)
                        and all(isinstance(item, (str, int, float, bool)) for item in value)
                    )
                )
            }
            if isinstance(raw_diagnostics, dict)
            else None
        )
        summary = {
            "schema_version": "1.0",
            "purpose": "scholartrace-sp04-luna-direct-report-smoke",
            "passed": False,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "diagnostics": diagnostics,
            "cause_type": type(exc.__cause__).__name__ if exc.__cause__ else None,
            "raw_report_stored": False,
            "notes": ["No comparison requests were sent after this smoke failed."],
        }
        write_json(OUTPUT, summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
