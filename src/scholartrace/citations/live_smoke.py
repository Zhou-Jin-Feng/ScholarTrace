"""Bounded OpenAlex citation smoke with a cache-only replay."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx

from scholartrace.citations.models import CitationExpansionResult, CitationSeed
from scholartrace.citations.provider import OpenAlexCitationProvider
from scholartrace.search.cache import JsonFileResponseCache
from scholartrace.search.http import AcademicHttpClient
from scholartrace.search.models import SourcePolicy


async def run_openalex_live_smoke(
    *,
    seeds: list[CitationSeed],
    cache_dir: Path,
    api_key: str | None,
    max_discovered_papers: int = 10,
) -> dict[str, object]:
    if not seeds or len(seeds) > 2:
        raise ValueError("M4 online smoke requires one or two bounded seed papers")
    started_at = datetime.now(UTC)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        provider = OpenAlexCitationProvider(
            AcademicHttpClient(
                source="openalex",
                client=client,
                cache=JsonFileResponseCache(cache_dir),
                policy=SourcePolicy(
                    max_network_requests=4,
                    min_interval_seconds=0.1,
                    timeout_seconds=30,
                    max_attempts=2,
                    base_backoff_seconds=0.5,
                    max_backoff_seconds=2,
                    jitter_ratio=0.1,
                    max_response_bytes=5_000_000,
                ),
            ),
            api_key=api_key,
            max_discovered_papers=max_discovered_papers,
        )
        first = await provider.expand_seeds(seeds)
        replay = await provider.expand_seeds(seeds)
    return build_live_smoke_summary(
        first=first,
        replay=replay,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        api_key_configured=api_key is not None,
        max_discovered_papers=max_discovered_papers,
    )


def build_live_smoke_summary(
    *,
    first: CitationExpansionResult,
    replay: CitationExpansionResult,
    started_at: datetime,
    completed_at: datetime,
    api_key_configured: bool,
    max_discovered_papers: int,
) -> dict[str, object]:
    first_edge_ids = [edge.edge_id for edge in first.edges]
    replay_edge_ids = [edge.edge_id for edge in replay.edges]
    network_attempts = sum(record.attempts for record in first.request_records)
    live_network_observed = network_attempts > 0 and any(
        not record.cache_hit for record in first.request_records
    )
    cached_replay_passed = (
        first_edge_ids == replay_edge_ids
        and len(replay.request_records) == len(first.request_records)
        and all(record.cache_hit and record.attempts == 0 for record in replay.request_records)
    )
    provenance_complete = all(
        edge.request_id and edge.response_sha256 and edge.source_work_id
        for edge in first.edges
    )
    request_statuses = [
        {
            "request_id": record.request_id,
            "status": record.status,
            "attempts": record.attempts,
            "cache_hit": record.cache_hit,
            "duration_seconds": round(record.duration_seconds, 3),
            "candidate_count": record.candidate_count,
            "error_code": record.error_code,
            "provider_reported_cost_usd": record.provider_reported_cost_usd,
        }
        for record in first.request_records
    ]
    requests_acceptable = bool(first.request_records) and all(
        record.status in {"succeeded", "empty"} for record in first.request_records
    )
    unique_cited_ids = {edge.cited_paper_id for edge in first.edges}
    metadata_requested_count = min(len(unique_cited_ids), max_discovered_papers)
    deferred_due_to_bound_count = max(
        0, len(unique_cited_ids) - metadata_requested_count
    )
    requested_unresolved_count = max(
        0, len(first.unresolved_openalex_ids) - deferred_due_to_bound_count
    )
    overall_resolution_ratio = (
        len(first.discovered_candidates) / len(unique_cited_ids)
        if unique_cited_ids
        else 0.0
    )
    bounded_resolution_ratio = (
        len(first.discovered_candidates) / metadata_requested_count
        if metadata_requested_count
        else 0.0
    )
    passed = all(
        (
            first.outcome in {"succeeded", "degraded"},
            bool(first.edges),
            requests_acceptable,
            live_network_observed,
            provenance_complete,
            cached_replay_passed,
            not first.missing_reference_paper_ids,
        )
    )
    return {
        "schema_version": "1.0",
        "generated_at": completed_at.isoformat(),
        "fixture_kind": "bounded_openalex_citation_live_smoke",
        "quality_scope": "Online engineering smoke; not a citation recall benchmark.",
        "credential_mode": "api_key" if api_key_configured else "anonymous",
        "api_key_configured": api_key_configured,
        "seed_paper_count": len(first.seed_paper_ids),
        "max_discovered_papers": max_discovered_papers,
        "logical_request_count": len(first.request_records),
        "network_attempts": network_attempts,
        "request_statuses": request_statuses,
        "citation_edge_count": len(first.edges),
        "source_work_count": len({edge.source_work_id for edge in first.edges}),
        "unique_cited_paper_count": len(unique_cited_ids),
        "metadata_requested_count": metadata_requested_count,
        "discovered_candidate_count": len(first.discovered_candidates),
        "bounded_metadata_resolution_ratio": round(bounded_resolution_ratio, 6),
        "overall_metadata_resolution_ratio": round(overall_resolution_ratio, 6),
        "deferred_due_to_bound_count": deferred_due_to_bound_count,
        "requested_unresolved_count": requested_unresolved_count,
        "unresolved_openalex_id_count": len(first.unresolved_openalex_ids),
        "missing_reference_paper_count": len(first.missing_reference_paper_ids),
        "provider_outcome": first.outcome,
        "provenance_complete": provenance_complete,
        "cached_replay_passed": cached_replay_passed,
        "provider_reported_cost_usd": sum(
            record.provider_reported_cost_usd for record in first.request_records
        ),
        "billed_api_cost_cny": 0.0,
        "duration_seconds": round((completed_at - started_at).total_seconds(), 3),
        "passed": passed,
        "notes": [
            "Raw OpenAlex responses are stored only in the ignored runtime cache.",
            "The report contains no API key, abstracts, titles, or raw response bodies.",
            "A bounded smoke proves live compatibility, not complete citation recall.",
            "No DocuMind service, local generation model, or paid model route was called.",
        ],
    }
