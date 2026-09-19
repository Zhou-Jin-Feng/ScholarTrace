"""Shared bounded helpers for live arXiv-to-DocuMind Evidence collection."""

from __future__ import annotations

import hashlib
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from scholartrace.contracts import DocuMindBinding, Paper, Sha256
from scholartrace.documind_compatibility import supports_documind_retrieve
from scholartrace.evidence.bindings import DocuMindBindingRepository

MAX_PDF_BYTES = 30 * 1024 * 1024
MAX_REDIRECTS = 3
ARXIV_ALLOWED_HOSTS = frozenset({"arxiv.org", "export.arxiv.org"})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
ARXIV_SOURCE_PATTERN = re.compile(
    r"^https?://(?:export\.)?arxiv\.org/abs/(?P<version>\d{4}\.\d{4,5}v\d+)$"
)


class FullTextAcquisitionError(ValueError):
    """A public full-text acquisition request failed a declared policy."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DocumentCleanupError(RuntimeError):
    """One or more DocuMind cleanup requests failed after best-effort cleanup."""

    def __init__(self, failed_document_keys: list[str], cleaned_count: int) -> None:
        self.failed_document_keys = tuple(failed_document_keys)
        self.cleaned_count = cleaned_count
        super().__init__("DocuMind cleanup failed for " + ", ".join(self.failed_document_keys))


@dataclass(frozen=True, slots=True)
class PdfAcquisition:
    path: Path
    filename: str
    source_url: str
    size_bytes: int
    sha256: Sha256
    reused: bool


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


class _DeletionResponse(_LenientModel):
    status: str
    document_key: Sha256
    cleanup_pending: bool = False


class _DocumentDetail(_LenientModel):
    document_key: Sha256
    status: str
    active_index_id: Sha256 | None
    chunk_count: int = Field(ge=0)


def arxiv_pdf_url(paper: Paper) -> tuple[str, str]:
    if paper.access_level != "fulltext":
        raise FullTextAcquisitionError(
            "access_policy",
            f"paper is not authorized for fulltext acquisition: {paper.canonical_paper_id}",
        )
    sources = [item for item in paper.sources if item.source == "arxiv"]
    if len(sources) > 1:
        raise ValueError(f"paper requires one arXiv source: {paper.canonical_paper_id}")
    if len(sources) == 1:
        match = ARXIV_SOURCE_PATTERN.fullmatch(sources[0].source_id)
        if match is not None:
            version = match.group("version")
            # arXiv serves the canonical extension-less route; the historical
            # ".pdf" suffix answers 301, which the metered live transport treats as
            # an unconfirmed (uncertain) effect instead of following it.
            return f"https://arxiv.org/pdf/{version}", f"{version}.pdf"
    if paper.arxiv_id is not None:
        # Reviewed fallback: metadata providers expose the arXiv identifier without
        # its version. arXiv answers the identifier route directly and the acquired
        # bytes are hashed, so the run records exactly which document it read.
        # Only modern identifiers are supported; legacy ids containing "/" are out of scope.
        if re.fullmatch(r"\d{4}\.\d{4,5}", paper.arxiv_id) is None:
            raise FullTextAcquisitionError(
                "source_policy",
                f"arXiv identifier is not a modern public identifier: {paper.arxiv_id}",
            )
        return (
            f"https://arxiv.org/pdf/{paper.arxiv_id}",
            f"{paper.arxiv_id}.pdf",
        )
    raise ValueError(f"paper requires one arXiv source: {paper.canonical_paper_id}")


def _validate_arxiv_url(url: str, *, code: str = "source_policy") -> None:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise FullTextAcquisitionError(code, f"arXiv URL has invalid authority: {url}") from exc
    if parsed.scheme != "https" or hostname not in ARXIV_ALLOWED_HOSTS:
        message = (
            f"arXiv redirect left the allowlist: {url}"
            if code == "redirect_policy"
            else f"arXiv URL is outside the HTTPS allowlist: {url}"
        )
        raise FullTextAcquisitionError(code, message)
    if parsed.username or parsed.password or port not in (None, 443):
        raise FullTextAcquisitionError(code, f"arXiv URL has unsafe authority: {url}")


def sha256_file(path: Path) -> Sha256:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise FullTextAcquisitionError("file_error", f"cannot read PDF: {path.name}") from exc
    return digest.hexdigest()


def validate_pdf(path: Path) -> int:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise FullTextAcquisitionError("file_error", f"cannot inspect PDF: {path.name}") from exc
    if size < 5 or size > MAX_PDF_BYTES:
        raise FullTextAcquisitionError("size_limit", f"invalid PDF size: {path.name}")
    try:
        with path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise FullTextAcquisitionError("invalid_pdf", f"download is not a PDF: {path.name}")
    except OSError as exc:
        raise FullTextAcquisitionError("file_error", f"cannot read PDF: {path.name}") from exc
    return size


async def download_pdf(
    client: httpx.AsyncClient,
    *,
    paper: Paper,
    output_dir: Path,
) -> tuple[Path, int]:
    acquired = await acquire_pdf(client, paper=paper, output_dir=output_dir)
    return acquired.path, acquired.size_bytes


async def acquire_pdf(
    client: httpx.AsyncClient,
    *,
    paper: Paper,
    output_dir: Path,
) -> PdfAcquisition:
    url, filename = arxiv_pdf_url(paper)
    _validate_arxiv_url(url)
    destination = output_dir / filename
    if destination.is_symlink():
        raise FullTextAcquisitionError("file_error", f"refusing symlink destination: {filename}")
    if destination.exists() and not destination.is_file():
        raise FullTextAcquisitionError("file_error", f"destination is not a file: {filename}")
    if destination.exists() and destination.is_file():
        try:
            size = validate_pdf(destination)
            return PdfAcquisition(
                path=destination,
                filename=filename,
                source_url=url,
                size_bytes=size,
                sha256=sha256_file(destination),
                reused=True,
            )
        except FullTextAcquisitionError:
            # A stale or partial destination is replaced only after a new valid
            # response has been fully written and verified.
            pass
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FullTextAcquisitionError(
            "file_error", f"cannot create PDF directory: {output_dir}"
        ) from exc
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise FullTextAcquisitionError("file_error", f"unsafe PDF directory: {output_dir}")
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
            current_url = url
            for redirect_index in range(MAX_REDIRECTS + 1):
                try:
                    async with client.stream(
                        "GET", current_url, follow_redirects=False
                    ) as response:
                        if response.status_code in REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location:
                                raise FullTextAcquisitionError(
                                    "redirect_policy", f"arXiv redirect has no Location: {filename}"
                                )
                            if redirect_index >= MAX_REDIRECTS:
                                raise FullTextAcquisitionError(
                                    "redirect_policy", f"arXiv redirect limit exceeded: {filename}"
                                )
                            next_url = urljoin(current_url, location)
                            _validate_arxiv_url(next_url, code="redirect_policy")
                            current_url = next_url
                            continue
                        if response.status_code < 200 or response.status_code >= 300:
                            raise FullTextAcquisitionError(
                                "http_error",
                                f"arXiv returned HTTP {response.status_code}: {filename}",
                            )
                        _validate_arxiv_url(str(response.url), code="redirect_policy")
                        content_type = response.headers.get("content-type", "")
                        media_type = content_type.split(";", maxsplit=1)[0].strip().lower()
                        if media_type != "application/pdf":
                            raise FullTextAcquisitionError(
                                "content_type", f"arXiv response is not a PDF: {filename}"
                            )
                        content_length = response.headers.get("content-length")
                        declared_size: int | None = None
                        if content_length is not None:
                            try:
                                declared_size = int(content_length)
                            except ValueError as exc:
                                raise FullTextAcquisitionError(
                                    "content_length", f"arXiv size header is invalid: {filename}"
                                ) from exc
                            if declared_size < 0 or declared_size > MAX_PDF_BYTES:
                                raise FullTextAcquisitionError(
                                    "size_limit", f"arXiv PDF exceeds size limit: {filename}"
                                )
                        async for block in response.aiter_bytes():
                            total += len(block)
                            if total > MAX_PDF_BYTES:
                                raise FullTextAcquisitionError(
                                    "size_limit", f"arXiv PDF exceeds size limit: {filename}"
                                )
                            temporary.write(block)
                        if declared_size is not None and total != declared_size:
                            raise FullTextAcquisitionError(
                                "content_length", f"arXiv response size changed: {filename}"
                            )
                        break
                except FullTextAcquisitionError:
                    raise
                except httpx.HTTPError as exc:
                    raise FullTextAcquisitionError(
                        "transport_error", f"arXiv download failed: {filename}"
                    ) from exc
            else:
                raise FullTextAcquisitionError(
                    "redirect_policy", f"arXiv redirect failed: {filename}"
                )
        temporary_path = Path(temporary_name)
        validate_pdf(temporary_path)
        temporary_path.replace(destination)
        size = validate_pdf(destination)
        digest = sha256_file(destination)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return PdfAcquisition(
        path=destination,
        filename=filename,
        source_url=url,
        size_bytes=size,
        sha256=digest,
        reused=False,
    )


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
    documind_version: str,
    expected_source_sha256: dict[str, Sha256] | None = None,
    multipart_boundary: str | None = None,
) -> tuple[list[DocuMindBinding], int]:
    if multipart_boundary is not None and not re.fullmatch(
        r"[a-zA-Z0-9]{1,70}", multipart_boundary
    ):
        raise ValueError("multipart boundary must be a bounded ASCII identifier")
    if not supports_documind_retrieve(documind_version):
        raise ValueError("unsupported DocuMind provider version")
    list_response = await client.get(f"{base_url}/api/v1/documents")
    document_list = await _json_response(list_response, _DocumentList)
    assert isinstance(document_list, _DocumentList)
    existing_keys = {item.document_key for item in document_list.items}
    bindings: list[DocuMindBinding] = []
    total_chunks = 0

    for paper in papers:
        path = document_paths[paper.canonical_paper_id]
        validate_pdf(path)
        source_sha256 = sha256_file(path)
        expected = (
            expected_source_sha256.get(paper.canonical_paper_id) if expected_source_sha256 else None
        )
        if expected is not None and source_sha256 != expected:
            raise FullTextAcquisitionError(
                "source_hash_mismatch", f"acquired PDF changed before ingest: {path.name}"
            )
        with path.open("rb") as source:
            response = await client.post(
                f"{base_url}/api/v1/documents",
                files={"file": (path.name, source, "application/pdf")},
                headers=({"content-type": f"multipart/form-data; boundary={multipart_boundary}"}
                         if multipart_boundary else None),
            )
        if sha256_file(path) != source_sha256:
            raise FullTextAcquisitionError(
                "source_hash_mismatch", f"acquired PDF changed during ingest: {path.name}"
            )
        ingestion = await _json_response(response, _IngestionResponse)
        assert isinstance(ingestion, _IngestionResponse)
        if ingestion.source_sha256 != source_sha256:
            raise FullTextAcquisitionError(
                "provider_hash_mismatch", f"DocuMind source hash mismatch: {path.name}"
            )
        if ingestion.document_key not in existing_keys:
            newly_created.append(ingestion.document_key)
            existing_keys.add(ingestion.document_key)

        detail_response = await client.get(f"{base_url}/api/v1/documents/{ingestion.document_key}")
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
            documind_version=documind_version,
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
    failed: list[str] = []
    for document_key in reversed(document_keys):
        try:
            response = await client.delete(f"{base_url}/api/v1/documents/{document_key}")
            if response.status_code in (404, 204):
                cleaned += 1
            elif response.status_code == 200:
                deletion = _DeletionResponse.model_validate_json(response.content)
                if (
                    deletion.status == "deleted"
                    and deletion.document_key == document_key
                    and not deletion.cleanup_pending
                ):
                    cleaned += 1
                else:
                    failed.append(document_key)
            else:
                failed.append(document_key)
        except (httpx.HTTPError, ValueError):
            failed.append(document_key)
    if failed:
        raise DocumentCleanupError(failed, cleaned)
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
