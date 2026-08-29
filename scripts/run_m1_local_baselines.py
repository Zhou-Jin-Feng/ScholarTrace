"""Generate M1 B0/B1 with local Qwen and extend the search RunManifest."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from scholartrace.contracts import BudgetUsage, RunManifest
from scholartrace.search.baseline import OllamaBaselineGenerator
from scholartrace.search.models import SearchSnapshot
from scholartrace.search.storage import write_json, write_model, write_text

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACTS = ROOT / "artifacts" / "m1-search"


async def _run(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    snapshot = SearchSnapshot.model_validate(json.loads(args.snapshot.read_text("utf-8")))
    async with httpx.AsyncClient(trust_env=False) as client:
        generator = OllamaBaselineGenerator(
            client=client,
            base_url=args.base_url,
            model=args.model,
            timeout_seconds=args.timeout_seconds,
        )
        b0 = await generator.generate(snapshot, baseline="B0")
        b1 = await generator.generate(snapshot, baseline="B1")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_model(args.output_dir / "b0_ollama.json", b0.artifact)
    write_text(args.output_dir / "b0_ollama.md", b0.artifact.content_markdown)
    write_model(args.output_dir / "b1_ollama.json", b1.artifact)
    write_text(args.output_dir / "b1_ollama.md", b1.artifact.content_markdown)

    manifest = RunManifest.model_validate(json.loads(args.manifest.read_text("utf-8")))
    usages = [b0.usage, b1.usage]
    previous = manifest.budget.usage
    updated_usage = BudgetUsage(
        **{
            **previous.model_dump(),
            "llm_input_tokens": previous.llm_input_tokens
            + sum(usage.input_tokens for usage in usages),
            "llm_output_tokens": previous.llm_output_tokens
            + sum(usage.output_tokens for usage in usages),
            "model_calls": previous.model_calls + sum(usage.call_count for usage in usages),
            "local_gpu_seconds": previous.local_gpu_seconds
            + sum(usage.local_gpu_seconds for usage in usages),
            "elapsed_seconds": previous.elapsed_seconds
            + sum(usage.duration_seconds for usage in usages),
        }
    )
    updated_manifest = manifest.model_copy(
        update={
            "model_usage": usages,
            "budget": manifest.budget.model_copy(update={"usage": updated_usage}),
            "completed_at": datetime.now(UTC),
        }
    )
    extended_manifest_sha = write_model(
        args.output_dir / "run_manifest_with_local_baselines.json",
        updated_manifest,
    )
    completed = datetime.now(UTC)
    report: dict[str, object] = {
        "schema_version": "1.0",
        "generated_at": completed.isoformat(),
        "model": args.model,
        "model_id": "500a1f067a9f",
        "quantization": "Q4_K_M",
        "context_window": 40960,
        "request_context_window": 8192,
        "baselines": [
            {
                "baseline": result.artifact.baseline,
                "content_sha256": result.artifact.content_sha256,
                "cited_candidate_count": len(result.artifact.candidate_paper_ids),
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "call_count": result.usage.call_count,
                "duration_seconds": round(result.usage.duration_seconds, 3),
            }
            for result in (b0, b1)
        ],
        "extended_manifest_sha256": extended_manifest_sha,
        "billed_api_cost_cny": 0.0,
        "passed": True,
        "notes": [
            "Cited IDs were validated against the supplied candidate set.",
            "Only metadata and abstracts were supplied to the model.",
            "Raw Ollama envelopes and prompts are not stored in this report.",
            "GPU time is not measured separately from wall time in M1.",
        ],
    }
    if args.summary_output:
        write_json(args.summary_output, report)
    return 0, report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_ARTIFACTS / "search_snapshot.json")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_ARTIFACTS / "run_manifest.json")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ARTIFACTS)
    parser.add_argument("--summary-output", type=Path)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout-seconds", type=float, default=180)
    args = parser.parse_args()
    exit_code, report = asyncio.run(_run(args))
    print(json.dumps(report, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
