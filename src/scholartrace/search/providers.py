"""Academic metadata source adapters with sanitized request audits."""

from __future__ import annotations

import hashlib
import json
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Any, Protocol

from scholartrace.search.errors import AcademicSourceError
from scholartrace.search.http import AcademicHttpClient, HttpPayload
from scholartrace.search.identifiers import (
    arxiv_id_from_doi,
    candidate_id,
    normalize_doi,
    normalize_openalex_id,
    normalize_semantic_scholar_id,
    parse_arxiv_id,
)
from scholartrace.search.models import (
    PaperCandidate,
    SearchRequest,
    SourceName,
    SourceRequestRecord,
    SourceSearchResult,
    SourceStatus,
)

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"


class AcademicSource(Protocol):
    name: SourceName

    async def search(self, request: SearchRequest) -> SourceSearchResult: ...


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _calendar_date(value: Any) -> date | None:
    """Only retain complete valid dates; missing precision is never invented."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def candidate_within_dates(candidate: PaperCandidate, request: SearchRequest) -> bool:
    if request.from_year is not None and candidate.publication_year < request.from_year:
        return False
    if request.to_year is not None and candidate.publication_year > request.to_year:
        return False
    cutoff = request.published_before
    if cutoff is None:
        return True
    # A year-only record can prove eligibility only once that whole year ended.
    published = candidate.publication_date or date(candidate.publication_year, 12, 31)
    if published > cutoff:
        return False
    if candidate.source == "arxiv":
        if candidate.arxiv_version != 1 and candidate.version_date is None:
            return False
        if candidate.version_date is not None and candidate.version_date > cutoff:
            return False
    return True


def _markup_text(value: str | None) -> str | None:
    if not value:
        return None
    parser = _TextExtractor()
    parser.feed(value)
    text = _clean_text(" ".join(parser.parts))
    return text or None


def _record_sha256(value: Any) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _request_id(source: SourceName, request: SearchRequest, params: Mapping[str, str]) -> str:
    serialized = json.dumps(
        {
            "source": source,
            "query": request.query,
            "from_year": request.from_year,
            "to_year": request.to_year,
            "published_before": request.published_before.isoformat()
            if request.published_before else None,
            "params": dict(sorted(params.items())),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"request:{source}:{hashlib.sha256(serialized).hexdigest()[:24]}"


class BaseAcademicSource:
    name: SourceName
    endpoint: str

    def __init__(self, http: AcademicHttpClient) -> None:
        if http.source != self.name:
            raise ValueError("HTTP source policy does not match provider")
        self.http = http

    async def search(self, request: SearchRequest) -> SourceSearchResult:
        public_params, private_params, headers = self.request_parts(request)
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        try:
            payload = await self.http.get(
                url=self.endpoint,
                public_params=public_params,
                private_params=private_params,
                headers=headers,
            )
            candidates, provider_cost = self.parse(payload)
            original_count = len(candidates)
            candidates = [c for c in candidates if candidate_within_dates(c, request)]
            status: SourceStatus = "succeeded" if candidates else "empty"
            audit = SourceRequestRecord(
                request_id=_request_id(self.name, request, public_params),
                source=self.name,
                public_url=self.endpoint,
                public_params=public_params,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                duration_seconds=payload.duration_seconds,
                cache_hit=payload.cache_hit,
                attempts=payload.attempts,
                status=status,
                http_status=payload.status_code,
                response_sha256=payload.response_sha256,
                candidate_count=len(candidates),
                public_reason=(
                    f"Date scope excluded {original_count - len(candidates)} candidates; "
                    "incomplete dates are accepted only when eligibility is provable."
                    if len(candidates) != original_count else None
                ),
                provider_reported_cost_usd=provider_cost,
            )
            return SourceSearchResult(source=self.name, request=audit, candidates=candidates)
        except AcademicSourceError as exc:
            return self._failed_result(
                request=request,
                public_params=public_params,
                started_at=started_at,
                duration_seconds=time.perf_counter() - started,
                error=exc,
            )
        except (
            ET.ParseError,
            UnicodeError,
            ValueError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
        ):
            error = AcademicSourceError(
                code="invalid_response",
                public_reason=f"{self.name} returned an invalid response",
                attempts=1,
            )
            return self._failed_result(
                request=request,
                public_params=public_params,
                started_at=started_at,
                duration_seconds=time.perf_counter() - started,
                error=error,
            )

    def _failed_result(
        self,
        *,
        request: SearchRequest,
        public_params: dict[str, str],
        started_at: datetime,
        duration_seconds: float,
        error: AcademicSourceError,
    ) -> SourceSearchResult:
        audit = SourceRequestRecord(
            request_id=_request_id(self.name, request, public_params),
            source=self.name,
            public_url=self.endpoint,
            public_params=public_params,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            duration_seconds=duration_seconds,
            cache_hit=False,
            attempts=error.attempts,
            status="failed",
            http_status=error.http_status,
            error_code=error.code,
            public_reason=error.public_reason,
        )
        return SourceSearchResult(source=self.name, request=audit, candidates=[])

    def request_parts(
        self, request: SearchRequest
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        raise NotImplementedError

    def parse(self, payload: HttpPayload) -> tuple[list[PaperCandidate], float]:
        raise NotImplementedError


class ArxivSource(BaseAcademicSource):
    name: SourceName = "arxiv"
    endpoint = "https://export.arxiv.org/api/query"

    def request_parts(
        self, request: SearchRequest
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        safe_query = request.query.replace('"', " ")
        params = {
            "search_query": f'all:"{safe_query}"',
            "start": "0",
            "max_results": str(request.max_results),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        return params, {}, {"User-Agent": "ScholarTrace/0.2 metadata-client"}

    def parse(self, payload: HttpPayload) -> tuple[list[PaperCandidate], float]:
        root = ET.fromstring(payload.body)
        candidates: list[PaperCandidate] = []
        for entry in root.findall(f"{ATOM}entry"):
            source_id = _clean_text(entry.findtext(f"{ATOM}id"))
            title = _clean_text(entry.findtext(f"{ATOM}title"))
            published = _clean_text(entry.findtext(f"{ATOM}published"))
            authors = [
                _clean_text(author.findtext(f"{ATOM}name"))
                for author in entry.findall(f"{ATOM}author")
            ]
            authors = [author for author in authors if author]
            arxiv_id, arxiv_version = parse_arxiv_id(source_id)
            if not source_id or not title or not published or not authors or arxiv_id is None:
                continue
            source_url = source_id
            for link in entry.findall(f"{ATOM}link"):
                if link.attrib.get("rel") == "alternate" and link.attrib.get("href"):
                    source_url = link.attrib["href"]
                    break
            doi = normalize_doi(entry.findtext(f"{ARXIV}doi"))
            abstract = _clean_text(entry.findtext(f"{ATOM}summary")) or None
            record_bytes = ET.tostring(entry, encoding="utf-8")
            candidates.append(
                PaperCandidate(
                    candidate_id=candidate_id(self.name, source_id),
                    source=self.name,
                    source_id=source_id,
                    title=title,
                    authors=authors,
                    publication_year=int(published[:4]),
                    publication_date=_calendar_date(published),
                    version_date=_calendar_date(entry.findtext(f"{ATOM}updated")),
                    doi=doi,
                    arxiv_id=arxiv_id,
                    arxiv_version=arxiv_version,
                    abstract=abstract,
                    access_level="abstract" if abstract else "metadata",
                    source_url=source_url,
                    retrieved_at=payload.retrieved_at,
                    record_sha256=hashlib.sha256(record_bytes).hexdigest(),
                )
            )
        return candidates, 0.0


class OpenAlexSource(BaseAcademicSource):
    name: SourceName = "openalex"
    endpoint = "https://api.openalex.org/works"
    select_fields = ",".join(
        (
            "id",
            "doi",
            "title",
            "publication_year",
            "publication_date",
            "authorships",
            "ids",
            "abstract_inverted_index",
            "primary_location",
            "type",
        )
    )

    def __init__(self, http: AcademicHttpClient, *, api_key: str | None = None) -> None:
        super().__init__(http)
        self.api_key = api_key

    def request_parts(
        self, request: SearchRequest
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        params = {
            "search": request.query,
            "per-page": str(request.max_results),
            "select": self.select_fields,
        }
        filters: list[str] = []
        if request.from_year is not None:
            filters.append(f"from_publication_date:{request.from_year}-01-01")
        if request.to_year is not None:
            filters.append(f"to_publication_date:{request.to_year}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        private = {"api_key": self.api_key} if self.api_key else {}
        return params, private, {"User-Agent": "ScholarTrace/0.2 metadata-client"}

    def parse(self, payload: HttpPayload) -> tuple[list[PaperCandidate], float]:
        document: Any = json.loads(payload.body)
        if not isinstance(document, dict) or not isinstance(document.get("results"), list):
            raise ValueError("OpenAlex response has no results list")
        meta_value = document.get("meta")
        meta: dict[Any, Any] = meta_value if isinstance(meta_value, dict) else {}
        provider_cost = float(meta.get("cost_usd", 0) or 0)
        candidates: list[PaperCandidate] = []
        for item in document["results"]:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("id") or "")
            title = _clean_text(str(item.get("title") or ""))
            year = item.get("publication_year")
            authorships = item.get("authorships")
            if (
                not source_id
                or not title
                or not isinstance(year, int)
                or not isinstance(authorships, list)
            ):
                continue
            authors: list[str] = []
            for authorship in authorships:
                if not isinstance(authorship, dict) or not isinstance(
                    authorship.get("author"), dict
                ):
                    continue
                display_name = _clean_text(str(authorship["author"].get("display_name") or ""))
                if display_name:
                    authors.append(display_name)
            if not authors:
                continue
            doi = normalize_doi(str(item.get("doi") or ""))
            ids_value = item.get("ids")
            ids: dict[Any, Any] = ids_value if isinstance(ids_value, dict) else {}
            raw_arxiv = str(ids.get("arxiv") or "")
            arxiv_id, arxiv_version = parse_arxiv_id(raw_arxiv)
            arxiv_id = arxiv_id or arxiv_id_from_doi(doi)
            openalex_id = normalize_openalex_id(source_id)
            abstract = self._abstract(item.get("abstract_inverted_index"))
            primary = item.get("primary_location")
            source_url = None
            if isinstance(primary, dict) and primary.get("landing_page_url"):
                source_url = str(primary["landing_page_url"])
            candidates.append(
                PaperCandidate(
                    candidate_id=candidate_id(self.name, source_id),
                    source=self.name,
                    source_id=source_id,
                    title=title,
                    authors=authors,
                    publication_year=year,
                    publication_date=_calendar_date(item.get("publication_date")),
                    doi=doi,
                    arxiv_id=arxiv_id,
                    arxiv_version=arxiv_version,
                    openalex_id=openalex_id,
                    abstract=abstract,
                    access_level="abstract" if abstract else "metadata",
                    source_url=source_url,
                    retrieved_at=payload.retrieved_at,
                    record_sha256=_record_sha256(item),
                )
            )
        return candidates, provider_cost

    @staticmethod
    def _abstract(value: Any) -> str | None:
        if not isinstance(value, dict):
            return None
        positions: list[tuple[int, str]] = []
        for token, raw_positions in value.items():
            if not isinstance(token, str) or not isinstance(raw_positions, list):
                continue
            for position in raw_positions:
                if isinstance(position, int):
                    positions.append((position, token))
        positions.sort()
        text = " ".join(token for _, token in positions)
        return text or None


class CrossrefSource(BaseAcademicSource):
    name: SourceName = "crossref"
    endpoint = "https://api.crossref.org/works"
    select_fields = ",".join(
        (
            "DOI",
            "title",
            "author",
            "published",
            "abstract",
            "type",
            "URL",
            "alternative-id",
            "relation",
        )
    )

    def __init__(self, http: AcademicHttpClient, *, contact_email: str | None = None) -> None:
        super().__init__(http)
        self.contact_email = contact_email

    def request_parts(
        self, request: SearchRequest
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        params = {
            "query.title": request.query,
            "rows": str(request.max_results),
            "select": self.select_fields,
        }
        filters: list[str] = []
        if request.from_year is not None:
            filters.append(f"from-pub-date:{request.from_year}-01-01")
        if request.to_year is not None:
            filters.append(f"until-pub-date:{request.to_year}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        private = {"mailto": self.contact_email} if self.contact_email else {}
        agent = "ScholarTrace/0.2 (metadata research client)"
        if self.contact_email:
            agent = f"ScholarTrace/0.2 (mailto:{self.contact_email})"
        return params, private, {"User-Agent": agent}

    def parse(self, payload: HttpPayload) -> tuple[list[PaperCandidate], float]:
        document: Any = json.loads(payload.body)
        if not isinstance(document, dict) or not isinstance(document.get("message"), dict):
            raise ValueError("Crossref response has no message object")
        items = document["message"].get("items")
        if not isinstance(items, list):
            raise ValueError("Crossref response has no items list")
        candidates: list[PaperCandidate] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            doi = normalize_doi(str(item.get("DOI") or ""))
            titles = item.get("title")
            title = _clean_text(str(titles[0])) if isinstance(titles, list) and titles else ""
            authors_raw = item.get("author")
            authors: list[str] = []
            if isinstance(authors_raw, list):
                for author in authors_raw:
                    if not isinstance(author, dict):
                        continue
                    name = _clean_text(
                        " ".join(
                            part
                            for part in (
                                str(author.get("given") or ""),
                                str(author.get("family") or ""),
                            )
                            if part
                        )
                    )
                    if name:
                        authors.append(name)
            year = self._year(item.get("published"))
            if doi is None or not title or not authors or year is None:
                continue
            arxiv_id = arxiv_id_from_doi(doi)
            abstract = _markup_text(str(item.get("abstract") or ""))
            source_url = str(item.get("URL") or f"https://doi.org/{doi}")
            candidates.append(
                PaperCandidate(
                    candidate_id=candidate_id(self.name, doi),
                    source=self.name,
                    source_id=doi,
                    title=title,
                    authors=authors,
                    publication_year=year,
                    publication_date=self._date(item.get("published")),
                    doi=doi,
                    arxiv_id=arxiv_id,
                    abstract=abstract,
                    access_level="abstract" if abstract else "metadata",
                    source_url=source_url,
                    retrieved_at=payload.retrieved_at,
                    record_sha256=_record_sha256(item),
                )
            )
        return candidates, 0.0

    @staticmethod
    def _date(value: Any) -> date | None:
        if not isinstance(value, dict):
            return None
        parts = value.get("date-parts")
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], list):
            return None
        if len(parts[0]) != 3 or any(type(v) is not int for v in parts[0]):
            return None
        try:
            return date(*parts[0])
        except ValueError:
            return None

    @staticmethod
    def _year(value: Any) -> int | None:
        if not isinstance(value, dict):
            return None
        date_parts = value.get("date-parts")
        if (
            not isinstance(date_parts, list)
            or not date_parts
            or not isinstance(date_parts[0], list)
            or not date_parts[0]
            or not isinstance(date_parts[0][0], int)
        ):
            return None
        return date_parts[0][0]


class SemanticScholarSource(BaseAcademicSource):
    name: SourceName = "semantic_scholar"
    endpoint = "https://api.semanticscholar.org/graph/v1/paper/search"
    fields = "paperId,title,authors,year,publicationDate,abstract,externalIds,url,openAccessPdf"

    def __init__(self, http: AcademicHttpClient, *, api_key: str | None = None) -> None:
        super().__init__(http)
        self.api_key = api_key

    def request_parts(
        self, request: SearchRequest
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        params = {
            "query": request.query,
            "limit": str(request.max_results),
            "fields": self.fields,
        }
        headers = {"User-Agent": "ScholarTrace/0.2 metadata-client"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return params, {}, headers

    def parse(self, payload: HttpPayload) -> tuple[list[PaperCandidate], float]:
        document: Any = json.loads(payload.body)
        if not isinstance(document, dict) or not isinstance(document.get("data"), list):
            raise ValueError("Semantic Scholar response has no data list")
        candidates: list[PaperCandidate] = []
        for item in document["data"]:
            if not isinstance(item, dict):
                continue
            paper_id = normalize_semantic_scholar_id(str(item.get("paperId") or ""))
            title = _clean_text(str(item.get("title") or ""))
            year = item.get("year")
            authors_raw = item.get("authors")
            authors = (
                [
                    _clean_text(str(author.get("name") or ""))
                    for author in authors_raw
                    if isinstance(author, dict)
                ]
                if isinstance(authors_raw, list)
                else []
            )
            authors = [author for author in authors if author]
            if paper_id is None or not title or not isinstance(year, int) or not authors:
                continue
            external_value = item.get("externalIds")
            external: dict[Any, Any] = external_value if isinstance(external_value, dict) else {}
            doi = normalize_doi(str(external.get("DOI") or ""))
            arxiv_id, arxiv_version = parse_arxiv_id(str(external.get("ArXiv") or ""))
            arxiv_id = arxiv_id or arxiv_id_from_doi(doi)
            abstract = _clean_text(str(item.get("abstract") or "")) or None
            candidates.append(
                PaperCandidate(
                    candidate_id=candidate_id(self.name, paper_id),
                    source=self.name,
                    source_id=paper_id,
                    title=title,
                    authors=authors,
                    publication_year=year,
                    publication_date=_calendar_date(item.get("publicationDate")),
                    doi=doi,
                    arxiv_id=arxiv_id,
                    arxiv_version=arxiv_version,
                    semantic_scholar_id=paper_id,
                    abstract=abstract,
                    access_level="abstract" if abstract else "metadata",
                    source_url=str(item.get("url") or "") or None,
                    retrieved_at=payload.retrieved_at,
                    record_sha256=_record_sha256(item),
                )
            )
        return candidates, 0.0
