"""Run the three-paper M2 contract fixture against the real local Qwen model."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx

from scholartrace.contracts import DocuMindBinding, Paper
from scholartrace.evidence.analysis import OllamaPaperAnalyzer
from scholartrace.evidence.artifacts import persist_m2_artifacts
from scholartrace.evidence.bindings import DocuMindBindingRepository
from scholartrace.evidence.client import DocuMindClient
from scholartrace.evidence.pipeline import M2EvidencePipeline
from scholartrace.search.storage import source_tree_sha256, write_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "documind" / "m2_three_papers.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "m2-evidence-fixture"
DEFAULT_SUMMARY = ROOT / "evaluation" / "reports" / "m2_local_fixture_smoke.json"


def _git(*args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


async def _run(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    fixture = json.loads(args.fixture.read_text("utf-8"))
    cases = fixture["papers"]
    by_document = {case["binding"]["document_key"]: case["retrieve_response"] for case in cases}
    retrieval_requests: list[dict[str, object]] = []

    def documind_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        retrieval_requests.append(body)
        return httpx.Response(200, json=by_document[body["document_key"]], request=request)

    repository = DocuMindBindingRepository(args.output_dir / "bindings.sqlite3")
    papers: list[Paper] = []
    for case in cases:
        paper = Paper.model_validate(case["paper"])
        binding = DocuMindBinding.model_validate(case["binding"])
        repository.put(binding)
        papers.append(paper)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(documind_handler)) as documind_http,
        httpx.AsyncClient(trust_env=False) as ollama_http,
    ):
        pipeline = M2EvidencePipeline(
            client=DocuMindClient(client=documind_http),
            bindings=repository,
            analyzer=OllamaPaperAnalyzer(
                client=ollama_http,
                base_url=args.ollama_url,
                model=args.model,
                model_version=args.model_version,
                timeout_seconds=args.timeout_seconds,
            ),
            max_concurrency=2,
        )
        result = await pipeline.run(papers=papers, question=fixture["question"])

    persisted = persist_m2_artifacts(
        root=ROOT,
        output_dir=args.output_dir,
        result=result,
        git_commit=_git("rev-parse", "HEAD"),
        worktree_dirty=bool(_git("status", "--porcelain", "--untracked-files=no")),
        source_tree_sha256=source_tree_sha256(ROOT),
    )
    evidence = [item for analysis in result.report.analyses for item in analysis.evidence]
    passed = (
        len(result.report.analyses) == 3
        and len(retrieval_requests) == 3
        and len(result.model_usage) == 3
        and all(
            item.document_key
            and item.index_id
            and item.source_sha256
            and item.chunk_id
            and item.page_number
            and item.chunk_content_sha256
            for item in evidence
        )
    )
    summary: dict[str, object] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "fixture_kind": fixture["fixture_kind"],
        "quality_scope": fixture["quality_scope"],
        "documind_mode": "mock_provider_responses",
        "documind_online_service_used": False,
        "documind_provider_baseline": fixture["provider_baseline"],
        "local_model": {
            "name": args.model,
            "model_id": args.model_version,
            "quantization": "Q4_K_M",
            "request_context_window": 8192,
        },
        "paper_count": len(result.report.analyses),
        "paper_ids": [item.paper_card.canonical_paper_id for item in result.report.analyses],
        "evidence_count": len(evidence),
        "retrieval_attempts": sum(item.attempts for item in result.retrieval_audits),
        "model_calls": sum(item.call_count for item in result.model_usage),
        "structured_repairs": sum(item.structured_repair_count for item in result.model_usage),
        "input_tokens": sum(item.input_tokens for item in result.model_usage),
        "output_tokens": sum(item.output_tokens for item in result.model_usage),
        "model_duration_seconds": round(
            sum(item.duration_seconds for item in result.model_usage), 3
        ),
        "pipeline_duration_seconds": round(
            (result.completed_at - result.started_at).total_seconds(), 3
        ),
        "report_content_sha256": result.report.content_sha256,
        "manifest_sha256": persisted.manifest_sha256,
        "billed_api_cost_cny": persisted.manifest.budget.usage.external_cost_cny,
        "passed": passed,
        "notes": [
            (
                "The three paper identities are real DEV-01 papers, but retrieval responses "
                "are fixtures."
            ),
            (
                "This smoke validates local structured extraction and provenance, not "
                "DocuMind retrieval quality."
            ),
            (
                "Only exact quotes from supplied chunks are accepted; raw Ollama envelopes "
                "are not stored."
            ),
            "GPU time is not measured separately from wall time in M2.",
        ],
    }
    write_json(args.summary_output, summary)
    print(json.dumps(summary, ensure_ascii=False))
    return (0 if passed else 1), summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--model-version", default="500a1f067a9f")
    parser.add_argument("--timeout-seconds", type=float, default=180)
    args = parser.parse_args()
    exit_code, _ = asyncio.run(_run(args))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
