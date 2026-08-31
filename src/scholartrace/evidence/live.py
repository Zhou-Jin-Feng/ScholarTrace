"""Shared bounded helpers for live arXiv-to-DocuMind Evidence collection."""

from __future__ import annotations

import hashlib
import re
import tempfile
import time
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field

from scholartrace.contracts import DocuMindBinding, Paper, Sha256
from scholartrace.evidence.bindings import DocuMindBindingRepository

MAX_PDF_BYTES = 30 * 1024 * 1024
ARXIV_SOURCE_PATTERN = re.compile(
    r"^https?://(?:export\.)?arxiv\.org/abs/(?P<version>\d{4}\.\d{4,5}v\d+)$"
)


class _LenientModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _DocumentListItem(_LenientModel):
    document_key: Sha256


class _DocumentList(_LenientModel):
    items: list[_DocumentListItem]


class _IngestionResponse(_LenientModel):
    status: str
    document_key: Sha256
    index_id: Sha256
    source_sha256: Sha256
    chunk_count: int = Field(ge=1)


class _DocumentDetail(_LenientModel):
    document_key: Sha256
    status: str
    active_index_id: Sha256 | None
    chunk_count: int = Field(ge=0)


def arxiv_pdf_url(paper: Paper) -> tuple[str, str]:
    sources = [item for item in paper.sources if item.source == "arxiv"]
    if len(sources) != 1:
        raise ValueError(f"paper requires one arXiv source: {paper.canonical_paper_id}")
    match = ARXIV_SOURCE_PATTERN.fullmatch(sources[0].source_id)
    if match is None:
        raise ValueError(f"paper requires a versioned arXiv source: {paper.canonical_paper_id}")
    version = match.group("version")
    return f"https://arxiv.org/pdf/{version}.pdf", f"{version}.pdf"


def validate_pdf(path: Path) -> int:
    size = path.stat().st_size
    if size < 5 or size > MAX_PDF_BYTES:
        raise ValueError(f"invalid PDF size: {path.name}")
    with path.open("rb") as source:
        if source.read(5) != b"%PDF-":
            raise ValueError(f"download is not a PDF: {path.name}")
    return size


async def download_pdf(
    client: httpx.AsyncClient,
    *,
    paper: Paper,
    output_dir: Path,
) -> tuple[Path, int]:
    url, filename = arxiv_pdf_url(paper)
    destination = output_dir / filename
    if destination.exists():
        return destination, validate_pdf(destination)

    output_dir.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{filename}.",
            suffix=".tmp",
            dir=output_dir,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            total = 0
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                if response.url.scheme != "https" or response.url.host not in {
                    "arxiv.org",
                    "export.arxiv.org",
                }:
                    raise ValueError(f"arXiv redirect left the allowlist: {filename}")
                if "application/pdf" not in response.headers.get("content-type", ""):
                    raise ValueError(f"arXiv response is not a PDF: {filename}")
                async for block in response.aiter_bytes():
                    total += len(block)
                    if total > MAX_PDF_BYTES:
                        raise ValueError(f"arXiv PDF exceeds size limit: {filename}")
                    temporary.write(block)
        temporary_path = Path(temporary_name)
        validate_pdf(temporary_path)
        temporary_path.replace(destination)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return destination, validate_pdf(destination)


async def _json_response(
    response: httpx.Response,
    model: type[_LenientModel],
) -> _LenientModel:
    response.raise_for_status()
    return model.model_validate_json(response.content)


async def ingest_papers(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    papers: list[Paper],
    document_paths: dict[str, Path],
    repository: DocuMindBindingRepository,
    newly_created: list[str],
) -> tuple[list[DocuMindBinding], int]:
    list_response = await client.get(f"{base_url}/api/v1/documents")
    document_list = await _json_response(list_response, _DocumentList)
    assert isinstance(document_list, _DocumentList)
    existing_keys = {item.document_key for item in document_list.items}
    bindings: list[DocuMindBinding] = []
    total_chunks = 0

    for paper in papers:
        path = document_paths[paper.canonical_paper_id]
        with path.open("rb") as source:
            response = await client.post(
                f"{base_url}/api/v1/documents",
                files={"file": (path.name, source, "application/pdf")},
            )
        ingestion = await _json_response(response, _IngestionResponse)
        assert isinstance(ingestion, _IngestionResponse)
        if ingestion.source_sha256 != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError(f"DocuMind source hash mismatch: {path.name}")
        if ingestion.document_key not in existing_keys:
            newly_created.append(ingestion.document_key)

        detail_response = await client.get(
            f"{base_url}/api/v1/documents/{ingestion.document_key}"
        )
        detail = await _json_response(detail_response, _DocumentDetail)
        assert isinstance(detail, _DocumentDetail)
        if (
            detail.status != "active"
            or detail.document_key != ingestion.document_key
            or detail.active_index_id != ingestion.index_id
            or detail.chunk_count != ingestion.chunk_count
        ):
            raise ValueError(f"DocuMind document is not active: {path.name}")

        binding = DocuMindBinding(
            canonical_paper_id=paper.canonical_paper_id,
            document_key=ingestion.document_key,
            index_id=ingestion.index_id,
            source_sha256=ingestion.source_sha256,
            documind_version="2.2.0",
            retrieval_schema_version="1.0",
        )
        current = repository.get(paper.canonical_paper_id)
        repository.put(
            binding,
            expected_previous_index_id=(current.index_id if current is not None else None),
        )
        bindings.append(binding)
        total_chunks += ingestion.chunk_count
    return bindings, total_chunks


async def cleanup_documents(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    document_keys: list[str],
) -> int:
    cleaned = 0
    for document_key in reversed(document_keys):
        response = await client.delete(f"{base_url}/api/v1/documents/{document_key}")
        response.raise_for_status()
        cleaned += 1
    return cleaned


async def warmup_embedding(
    client: httpx.AsyncClient,
    *,
    ollama_url: str,
    model: str,
) -> tuple[int, float]:
    started = time.perf_counter()
    response = await client.post(
        f"{ollama_url.rstrip('/')}/api/embed",
        json={
            "model": model,
            "input": "scholartrace m2 readiness",
            "keep_alive": "10m",
        },
    )
    response.raise_for_status()
    payload = response.json()
    embeddings = payload.get("embeddings") if isinstance(payload, dict) else None
    if (
        not isinstance(embeddings, list)
        or not embeddings
        or not isinstance(embeddings[0], list)
        or not embeddings[0]
    ):
        raise RuntimeError("Ollama embedding warmup returned no vector")
    return len(embeddings[0]), time.perf_counter() - started
