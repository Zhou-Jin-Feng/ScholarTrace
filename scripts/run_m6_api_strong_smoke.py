"""Run one bounded api-strong structured-plan compatibility smoke."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path

import httpx

from scholartrace.contracts import BudgetLimits
from scholartrace.model_provider import (
    ApiCallBudget,
    ApiCallCounter,
    OpenAICompatiblePlanGenerator,
    PlanGenerationResult,
    ProviderEndpointUnsupportedError,
    ProviderInferenceError,
    read_dotenv,
    resolve_provider_settings,
)
from scholartrace.search.storage import write_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts" / "reports" / "m6_api_strong_smoke.json"
DEFAULT_QUESTION = (
    "How do adaptive retrieval and evidence verification improve the faithfulness of "
    "retrieval-augmented generation systems?"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a bounded structured-output smoke against the approved api-strong model."
    )
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--protocol",
        choices=("auto", "responses", "chat_completions"),
        default="auto",
    )
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--max-output-tokens", type=int, default=900)
    parser.add_argument("--max-cost-cny", type=float, default=5)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> dict[str, object]:
    dotenv_values = read_dotenv(args.env_file)
    settings = resolve_provider_settings(
        cli_values={
            "base_url": args.base_url,
            "api_key": None,
            "timeout_seconds": None,
            "model": args.model,
        },
        environment=os.environ,
        dotenv_values=dotenv_values,
    )
    model = settings.model or "gpt-5.6-terra"
    if model != "gpt-5.6-terra":
        raise ValueError("M6 api-strong smoke is approved only for gpt-5.6-terra")
    if not settings.api_key.strip():
        raise ValueError("SCHOLARTRACE_API_KEY is required; no request was made")
    if args.timeout_seconds <= 0 or args.max_output_tokens <= 0 or args.max_cost_cny <= 0:
        raise ValueError("smoke timeout, output-token limit, and CNY budget must be positive")

    protocols = ["responses", "chat_completions"] if args.protocol == "auto" else [args.protocol]
    call_counter = ApiCallCounter(
        ApiCallBudget(
            max_calls=len(protocols),
            max_input_token_upper_bound=20_000,
            max_output_tokens=args.max_output_tokens,
            max_cost_cny=args.max_cost_cny,
        )
    )
    plan_limits = BudgetLimits(
        max_rounds=2,
        max_queries=4,
        max_candidate_papers=10,
        max_fulltext_papers=5,
        max_rag_calls_per_paper=3,
        max_concurrency=2,
        max_llm_input_tokens=20_000,
        max_llm_output_tokens=args.max_output_tokens,
        max_total_tokens=20_000 + args.max_output_tokens,
        max_api_calls=len(protocols),
        max_model_calls=len(protocols),
        max_cost_cny=args.max_cost_cny,
        max_duration_seconds=max(30, round(args.timeout_seconds * len(protocols))),
    )
    result: PlanGenerationResult | None = None
    unsupported_protocols: list[str] = []
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as http:
        for protocol in protocols:
            generator = OpenAICompatiblePlanGenerator(
                client=http,
                settings=settings,
                model=model,
                protocol=protocol,  # type: ignore[arg-type]
                call_counter=call_counter,
                plan_limits=plan_limits,
                retrieval_cutoff=date.today(),
                timeout_seconds=args.timeout_seconds,
            )
            try:
                result = await generator.generate_plan_with_usage(
                    task_id="task:m6-api-strong-smoke",
                    question=DEFAULT_QUESTION,
                    idempotency_key=f"effect:m6:api-strong-smoke:{protocol}",
                )
                break
            except ProviderEndpointUnsupportedError:
                unsupported_protocols.append(protocol)
                continue
    if result is None:
        raise ProviderInferenceError("provider has no supported approved inference endpoint")

    plan_json = json.dumps(
        result.plan.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    response_id_sha256 = (
        hashlib.sha256(result.response_id.encode("utf-8")).hexdigest()
        if result.response_id
        else None
    )
    passed = all(
        (
            result.requested_model == "gpt-5.6-terra",
            result.response_model == "gpt-5.6-terra",
            result.plan.task_id == "task:m6-api-strong-smoke",
            result.plan.status == "draft",
            2 <= len(result.plan.subquestions) <= 4,
            result.usage.input_tokens > 0,
            result.usage.output_tokens > 0,
            result.usage.output_tokens <= args.max_output_tokens,
            call_counter.attempted_calls <= len(protocols),
            result.reference_cost_cny <= args.max_cost_cny,
        )
    )
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().isoformat(),
        "fixture_kind": "bounded_api_strong_structured_plan_smoke",
        "quality_scope": "Online compatibility smoke; not a planning-quality benchmark.",
        "provider": "mxou.ai",
        "base_url": settings.base_url,
        "requested_model": result.requested_model,
        "response_model": result.response_model,
        "protocol": result.protocol,
        "unsupported_protocols": unsupported_protocols,
        "api_attempts": call_counter.attempted_calls,
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "cached_input_tokens": result.usage.cached_input_tokens,
        "reasoning_output_tokens": result.usage.reasoning_output_tokens,
        "total_tokens": result.usage.total_tokens,
        "duration_seconds": round(result.duration_seconds, 3),
        "reference_cost_cny": round(result.reference_cost_cny, 6),
        "approved_budget_cny": args.max_cost_cny,
        "provider_billed_cost_cny": None,
        "provider_billing_observability": "unavailable_in_response",
        "reference_pricing": {
            "source": "official_openai_gpt_5_6_terra_reference",
            "input_usd_per_million": 2.0,
            "output_usd_per_million": 12.0,
            "usd_to_cny_planning_rate": 7.5,
            "provider_multiplier": "unknown",
        },
        "response_id_sha256": response_id_sha256,
        "response_sha256": result.response_sha256,
        "plan_sha256": hashlib.sha256(plan_json).hexdigest(),
        "subquestion_count": len(result.plan.subquestions),
        "inclusion_criteria_count": len(result.plan.inclusion_criteria),
        "exclusion_criteria_count": len(result.plan.exclusion_criteria),
        "source_count": len(result.plan.sources),
        "raw_prompt_stored": False,
        "raw_response_stored": False,
        "passed": passed,
        "notes": [
            "The report contains hashes and aggregate metrics only.",
            "Provider billing and multiplier were not returned by the inference response.",
            "A passing result proves bounded protocol and schema compatibility, not quality.",
        ],
    }


def main() -> int:
    args = parse_args()
    try:
        summary = asyncio.run(run(args))
    except (ProviderInferenceError, ValueError) as exc:
        summary = {
            "schema_version": "1.0",
            "generated_at": datetime.now().astimezone().isoformat(),
            "fixture_kind": "bounded_api_strong_structured_plan_smoke",
            "passed": False,
            "error_code": type(exc).__name__,
            "raw_prompt_stored": False,
            "raw_response_stored": False,
        }
        write_json(args.output, summary)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 1
    write_json(args.output, summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
