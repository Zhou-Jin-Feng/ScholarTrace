"""Bounded OpenAlex provider for explicit outgoing citation edges."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Any

from scholartrace.citations.models import CitationEdge, CitationExpansionResult, CitationSeed
from scholartrace.contracts import Paper
from scholartrace.search.errors import AcademicSourceError
from scholartrace.search.http import AcademicHttpClient, HttpPayload
from scholartrace.search.identifiers import canonical_id, normalize_openalex_id
from scholartrace.search.models import PaperCandidate, SourceRequestRecord
from scholartrace.search.providers import OpenAlexSource


class OpenAlexCitationProvider:
    """Fetch seed works, then bounded metadata for their referenced works."""

    endpoint = "https://api.openalex.org/works"

    def __init__(
        self,
        http: AcademicHttpClient,
        *,
        api_key: str | None = None,
        max_discovered_papers: int = 100,
    ) -> None:
        if http.source != "openalex":
            raise ValueError("citation provider requires an OpenAlex HTTP policy")
        if max_discovered_papers < 1 or max_discovered_papers > 100:
            raise ValueError("max_discovered_papers must be between 1 and 100")
        self.http = http
        self.api_key = api_key
        self.max_discovered_papers = max_discovered_papers
        self._parser = OpenAlexSource(http, api_key=api_key)

    async def expand(self, papers: list[Paper]) -> CitationExpansionResult:
        if not papers:
            raise ValueError("citation expansion requires at least one seed paper")
        seed_ids = [paper.canonical_paper_id for paper in papers]
        if len(seed_ids) != len(set(seed_ids)):
            raise ValueError("citation seed paper IDs must be unique")
        seed_by_openalex = {
            normalized: paper.canonical_paper_id
            for paper in papers
            for normalized in (normalize_openalex_id(paper.openalex_id),)
            if normalized is not None
        }
        unsupported_seeds = sorted(
            paper.canonical_paper_id
            for paper in papers
            if normalize_openalex_id(paper.openalex_id) is None
        )
        if len(seed_by_openalex) != len(papers) - len(unsupported_seeds):
            raise ValueError("citation seed OpenAlex IDs must be unique")
        return await self._expand(
            seed_ids=seed_ids,
            seed_by_openalex=seed_by_openalex,
            unsupported_seeds=unsupported_seeds,
        )

    async def expand_seeds(self, seeds: list[CitationSeed]) -> CitationExpansionResult:
        if not seeds:
            raise ValueError("citation expansion requires at least one seed")
        seed_ids = [seed.canonical_paper_id for seed in seeds]
        if len(seed_ids) != len(set(seed_ids)):
            raise ValueError("citation seed paper IDs must be unique")
        seed_by_openalex = {
            seed.openalex_id: seed.canonical_paper_id for seed in seeds
        }
        if len(seed_by_openalex) != len(seeds):
            raise ValueError("citation seed OpenAlex IDs must be unique")
        return await self._expand(
            seed_ids=seed_ids,
            seed_by_openalex=seed_by_openalex,
            unsupported_seeds=[],
        )

    async def _expand(
        self,
        *,
        seed_ids: list[str],
        seed_by_openalex: dict[str, str],
        unsupported_seeds: list[str],
    ) -> CitationExpansionResult:
        if not seed_by_openalex:
            return CitationExpansionResult(
                seed_paper_ids=sorted(seed_ids),
                unresolved_openalex_ids=[],
                missing_reference_paper_ids=unsupported_seeds,
                outcome="failed",
            )

        seed_fetch = await self._fetch(sorted(seed_by_openalex), purpose="seeds")
        records = [seed_fetch.record]
        if seed_fetch.payload is None:
            return CitationExpansionResult(
                seed_paper_ids=sorted(seed_ids),
                request_records=records,
                missing_reference_paper_ids=unsupported_seeds,
                outcome="failed",
            )

        raw_edges, missing_reference_ids = self._raw_edges(
            seed_fetch.payload,
            seed_by_openalex=seed_by_openalex,
        )
        all_referenced_ids = sorted({cited for _, cited in raw_edges})
        referenced_ids = all_referenced_ids[: self.max_discovered_papers]
        cited_candidates: list[PaperCandidate] = []
        cited_payload: HttpPayload | None = None
        if referenced_ids:
            cited_fetch = await self._fetch(referenced_ids, purpose="references")
            records.append(cited_fetch.record)
            cited_candidates = cited_fetch.candidates
            cited_payload = cited_fetch.payload

        canonical_by_openalex = dict(seed_by_openalex)
        for candidate in cited_candidates:
            if candidate.openalex_id is None:
                continue
            canonical_by_openalex[candidate.openalex_id] = canonical_id(
                doi=candidate.doi,
                arxiv_id=candidate.arxiv_id,
                openalex_id=candidate.openalex_id,
                semantic_scholar_id=candidate.semantic_scholar_id,
            )
        unresolved = sorted(set(all_referenced_ids) - set(canonical_by_openalex))
        request_id = seed_fetch.record.request_id
        response_sha256 = seed_fetch.payload.response_sha256
        retrieved_at = seed_fetch.payload.retrieved_at
        edges = [
            self._edge(
                citing_paper_id=seed_by_openalex[citing],
                cited_paper_id=canonical_by_openalex.get(cited, f"openalex:{cited}"),
                source_work_id=citing,
                cited_work_id=cited,
                request_id=request_id,
                response_sha256=response_sha256,
                retrieved_at=retrieved_at,
            )
            for citing, cited in raw_edges
            if citing in seed_by_openalex and citing != cited
        ]
        failures = [record for record in records if record.status == "failed"]
        degraded = bool(
            unsupported_seeds
            or missing_reference_ids
            or unresolved
            or failures
            or (referenced_ids and cited_payload is None)
        )
        return CitationExpansionResult(
            seed_paper_ids=sorted(seed_ids),
            edges=sorted(edges, key=lambda item: item.edge_id),
            discovered_candidates=sorted(
                cited_candidates,
                key=lambda item: item.candidate_id,
            ),
            request_records=records,
            unresolved_openalex_ids=unresolved,
            missing_reference_paper_ids=sorted(
                set(unsupported_seeds) | set(missing_reference_ids)
            ),
            outcome="degraded" if degraded else "succeeded",
        )

    async def _fetch(self, openalex_ids: list[str], *, purpose: str) -> _FetchResult:
        public_params = {
            "filter": "openalex_id:" + "|".join(openalex_ids),
            "per-page": str(len(openalex_ids)),
            "select": OpenAlexSource.select_fields + ",referenced_works",
        }
        private_params = {"api_key": self.api_key} if self.api_key else {}
        request_id = self._request_id(purpose, public_params)
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        try:
            payload = await self.http.get(
                url=self.endpoint,
                public_params=public_params,
                private_params=private_params,
                headers={"User-Agent": "ScholarTrace/0.4 citation-client"},
            )
            candidates, provider_cost = self._parser.parse(payload)
            record = SourceRequestRecord(
                request_id=request_id,
                source="openalex",
                public_url=self.endpoint,
                public_params=public_params,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                duration_seconds=payload.duration_seconds,
                cache_hit=payload.cache_hit,
                attempts=payload.attempts,
                status="succeeded" if candidates else "empty",
                http_status=payload.status_code,
                response_sha256=payload.response_sha256,
                candidate_count=len(candidates),
                provider_reported_cost_usd=provider_cost,
            )
            return _FetchResult(payload=payload, candidates=candidates, record=record)
        except AcademicSourceError as exc:
            record = SourceRequestRecord(
                request_id=request_id,
                source="openalex",
                public_url=self.endpoint,
                public_params=public_params,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                duration_seconds=time.perf_counter() - started,
                cache_hit=False,
                attempts=exc.attempts,
                status="failed",
                http_status=exc.http_status,
                error_code=exc.code,
                public_reason=exc.public_reason,
            )
            return _FetchResult(payload=None, candidates=[], record=record)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeError):
            record = SourceRequestRecord(
                request_id=request_id,
                source="openalex",
                public_url=self.endpoint,
                public_params=public_params,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                duration_seconds=time.perf_counter() - started,
                cache_hit=False,
                attempts=1,
                status="failed",
                error_code="invalid_response",
                public_reason="openalex returned invalid citation metadata",
            )
            return _FetchResult(payload=None, candidates=[], record=record)

    @staticmethod
    def _raw_edges(
        payload: HttpPayload,
        *,
        seed_by_openalex: dict[str, str],
    ) -> tuple[list[tuple[str, str]], list[str]]:
        document: Any = json.loads(payload.body)
        if not isinstance(document, dict) or not isinstance(document.get("results"), list):
            raise ValueError("OpenAlex citation response has no results list")
        edges: set[tuple[str, str]] = set()
        seen_sources: set[str] = set()
        missing: list[str] = []
        for work in document["results"]:
            if not isinstance(work, dict):
                continue
            source_id = normalize_openalex_id(str(work.get("id") or ""))
            if source_id is None or source_id not in seed_by_openalex:
                continue
            seen_sources.add(source_id)
            if "referenced_works" not in work or not isinstance(work["referenced_works"], list):
                missing.append(seed_by_openalex[source_id])
                continue
            for raw_cited in work["referenced_works"]:
                cited_id = normalize_openalex_id(str(raw_cited))
                if cited_id is not None and cited_id != source_id:
                    edges.add((source_id, cited_id))
        missing.extend(
            seed_by_openalex[source_id]
            for source_id in set(seed_by_openalex) - seen_sources
        )
        return sorted(edges), sorted(set(missing))

    @staticmethod
    def _request_id(purpose: str, public_params: dict[str, str]) -> str:
        serialized = json.dumps(
            {"purpose": purpose, "params": public_params},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"request:openalex:citation:{hashlib.sha256(serialized).hexdigest()[:20]}"

    @staticmethod
    def _edge(
        *,
        citing_paper_id: str,
        cited_paper_id: str,
        source_work_id: str,
        cited_work_id: str,
        request_id: str,
        response_sha256: str,
        retrieved_at: datetime,
    ) -> CitationEdge:
        digest = hashlib.sha256(
            f"openalex\0{source_work_id}\0{cited_work_id}".encode()
        ).hexdigest()
        return CitationEdge(
            edge_id=f"citation:openalex:{digest[:24]}",
            citing_paper_id=citing_paper_id,
            cited_paper_id=cited_paper_id,
            source="openalex",
            source_work_id=source_work_id,
            request_id=request_id,
            response_sha256=response_sha256,
            retrieved_at=retrieved_at,
        )


class _FetchResult:
    def __init__(
        self,
        *,
        payload: HttpPayload | None,
        candidates: list[PaperCandidate],
        record: SourceRequestRecord,
    ) -> None:
        self.payload = payload
        self.candidates = candidates
        self.record = record
