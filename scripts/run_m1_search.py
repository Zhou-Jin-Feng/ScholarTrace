"""Run the M1 public-metadata search smoke and persist reproducible artifacts."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path

import httpx

from scholartrace.search.baseline import build_baselines
from scholartrace.search.cache import JsonFileResponseCache
from scholartrace.search.http import AcademicHttpClient
from scholartrace.search.manifest import build_run_manifest
from scholartrace.search.models import SearchRequest, SearchSnapshot, SourcePolicy
from scholartrace.search.pipeline import SearchPipeline, replay_snapshot
from scholartrace.search.providers import (
    ArxivSource,
    CrossrefSource,
    OpenAlexSource,
    SemanticScholarSource,
)
from scholartrace.search.storage import source_tree_sha256, write_json, write_model, write_text

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts" / "m1-search"
DEFAULT_CACHE = ROOT / "data" / "cache" / "academic"


def _git_value(*args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _git_dirty() -> bool:
    return bool(_git_value("status", "--porcelain", "--untracked-files=no"))


async def _run(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    started_at = datetime.now(UTC)
    cache = JsonFileResponseCache(args.cache_dir)
    async with AsyncExitStack() as stack:
        arxiv_client = await stack.enter_async_context(httpx.AsyncClient(follow_redirects=True))
        openalex_client = await stack.enter_async_context(httpx.AsyncClient(follow_redirects=True))
        crossref_client = await stack.enter_async_context(httpx.AsyncClient(follow_redirects=True))
        sources = [
            ArxivSource(
                AcademicHttpClient(
                    source="arxiv",
                    client=arxiv_client,
                    cache=cache,
                    policy=SourcePolicy(min_interval_seconds=3, max_network_requests=3),
                )
            ),
            OpenAlexSource(
                AcademicHttpClient(
                    source="openalex",
                    client=openalex_client,
                    cache=cache,
                    policy=SourcePolicy(min_interval_seconds=0.1, max_network_requests=3),
                ),
                api_key=os.getenv("OPENALEX_API_KEY"),
            ),
            CrossrefSource(
                AcademicHttpClient(
                    source="crossref",
                    client=crossref_client,
                    cache=cache,
                    policy=SourcePolicy(min_interval_seconds=1, max_network_requests=3),
                ),
                contact_email=os.getenv("SCHOLARTRACE_CONTACT_EMAIL"),
            ),
        ]
        if args.include_semantic_scholar:
            semantic_client = await stack.enter_async_context(
                httpx.AsyncClient(follow_redirects=True)
            )
            sources.append(
                SemanticScholarSource(
                    AcademicHttpClient(
                        source="semantic_scholar",
                        client=semantic_client,
                        cache=cache,
                        policy=SourcePolicy(min_interval_seconds=1, max_network_requests=3),
                    ),
                    api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY"),
                )
            )

        request = SearchRequest(
            query=args.query,
            max_results=args.max_results,
            from_year=args.from_year,
            to_year=args.to_year,
        )
        pipeline = SearchPipeline(
            sources,
            max_candidate_papers=args.max_candidate_papers,
            min_relevance_score=args.min_relevance_score,
        )
        first = await pipeline.run(request)
        cached_replay = await pipeline.run(request)

    replay_digest = replay_snapshot(first)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = args.output_dir / "search_snapshot.json"
    snapshot_file_sha = write_model(snapshot_path, first)
    b0, b1 = build_baselines(first, generated_at=first.generated_at)
    write_model(args.output_dir / "b0.json", b0)
    write_text(args.output_dir / "b0.md", b0.content_markdown)
    write_model(args.output_dir / "b1.json", b1)
    write_text(args.output_dir / "b1.md", b1.content_markdown)
    completed_at = datetime.now(UTC)
    manifest = build_run_manifest(
        root=ROOT,
        snapshot=first,
        snapshot_path=snapshot_path,
        snapshot_file_sha256=snapshot_file_sha,
        started_at=started_at,
        completed_at=completed_at,
        git_commit=_git_value("rev-parse", "HEAD"),
        worktree_dirty=_git_dirty(),
        source_tree_sha256=source_tree_sha256(ROOT),
    )
    manifest_sha = write_model(args.output_dir / "run_manifest.json", manifest)

    required_sources_passed = all(
        result.request.status in {"succeeded", "empty"} for result in first.source_results
    )
    cached_replay_passed = first.candidate_set_sha256 == cached_replay.candidate_set_sha256 and all(
        result.request.cache_hit for result in cached_replay.source_results
    )
    offline_replay_passed = replay_digest == first.candidate_set_sha256
    passed = (
        required_sources_passed
        and bool(first.ranked_papers)
        and cached_replay_passed
        and offline_replay_passed
    )
    summary: dict[str, object] = {
        "schema_version": "1.0",
        "generated_at": completed_at.isoformat(),
        "query": args.query,
        "sources": [
            {
                "source": result.source,
                "status": result.request.status,
                "candidate_count": len(result.candidates),
                "network_attempts": result.request.attempts,
                "cache_hit": result.request.cache_hit,
                "provider_reported_cost_usd": result.request.provider_reported_cost_usd,
                "error_code": result.request.error_code,
            }
            for result in first.source_results
        ],
        "normalized_paper_count": len(first.ranked_papers),
        "candidate_set_sha256": first.candidate_set_sha256,
        "cached_replay_passed": cached_replay_passed,
        "offline_replay_passed": offline_replay_passed,
        "provider_reported_cost_usd": sum(
            result.request.provider_reported_cost_usd for result in first.source_results
        ),
        "billed_api_cost_cny": 0.0,
        "manifest_sha256": manifest_sha,
        "passed": passed,
        "notes": [
            "Provider-reported request cost is not represented as a paid invoice.",
            "No paid model route was enabled or called.",
            "Raw source responses and credentials are not included in this summary.",
            "B0/B1 are deterministic M1 controls over metadata and abstracts only.",
        ],
    }
    if args.summary_output:
        write_json(args.summary_output, summary)
    return (0 if passed else 1), summary


def _replay(path: Path) -> int:
    snapshot = SearchSnapshot.model_validate(json.loads(path.read_text("utf-8")))
    digest = replay_snapshot(snapshot)
    passed = digest == snapshot.candidate_set_sha256
    print(
        json.dumps(
            {
                "snapshot_id": snapshot.snapshot_id,
                "expected": snapshot.candidate_set_sha256,
                "actual": digest,
                "passed": passed,
            },
            ensure_ascii=False,
        )
    )
    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--query",
        default="Corrective Retrieval Augmented Generation",
    )
    parser.add_argument("--max-results", type=int, default=3)
    parser.add_argument("--max-candidate-papers", type=int, default=50)
    parser.add_argument("--min-relevance-score", type=float, default=4.0)
    parser.add_argument("--from-year", type=int)
    parser.add_argument("--to-year", type=int)
    parser.add_argument("--include-semantic-scholar", action="store_true")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary-output", type=Path)
    parser.add_argument("--replay-snapshot", type=Path)
    args = parser.parse_args()
    if args.replay_snapshot:
        return _replay(args.replay_snapshot)
    exit_code, summary = asyncio.run(_run(args))
    print(json.dumps(summary, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
