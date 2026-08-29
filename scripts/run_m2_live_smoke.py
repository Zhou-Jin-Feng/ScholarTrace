"""Run the M2 evidence pipeline against live DocuMind and versioned arXiv PDFs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field

from scholartrace.contracts import DocuMindBinding, Paper, Sha256
from scholartrace.evidence.analysis import OllamaPaperAnalyzer
from scholartrace.evidence.artifacts import persist_m2_artifacts
from scholartrace.evidence.bindings import DocuMindBindingRepository
from scholartrace.evidence.client import DocuMindClient
from scholartrace.evidence.pipeline import M2EvidencePipeline
from scholartrace.search.storage import source_tree_sha256, write_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "documind" / "m2_three_papers.json"
DEFAULT_DOCUMENTS = ROOT / "artifacts" / "m2-live-documents"
DEFAULT_OUTPUT = ROOT / "artifacts" / "m2-evidence-live"
DEFAULT_SUMMARY = ROOT / "evaluation" / "reports" / "m2_live_documind_smoke.json"
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


def _arxiv_pdf_url(paper: Paper) -> tuple[str, str]:
    sources = [item for item in paper.sources if item.source == "arxiv"]
    if len(sources) != 1:
        raise ValueError(f"paper requires one arXiv source: {paper.canonical_paper_id}")
    match = ARXIV_SOURCE_PATTERN.fullmatch(sources[0].source_id)
    if match is None:
        raise ValueError(f"paper requires a versioned arXiv source: {paper.canonical_paper_id}")
    version = match.group("version")
    return f"https://arxiv.org/pdf/{version}.pdf", f"{version}.pdf"


def _validate_pdf(path: Path) -> int:
    size = path.stat().st_size
    if size < 5 or size > MAX_PDF_BYTES:
        raise ValueError(f"invalid PDF size: {path.name}")
    with path.open("rb") as source:
        if source.read(5) != b"%PDF-":
            raise ValueError(f"download is not a PDF: {path.name}")
    return size


async def _download_pdf(
    client: httpx.AsyncClient,
    *,
    paper: Paper,
    output_dir: Path,
) -> tuple[Path, int]:
    url, filename = _arxiv_pdf_url(paper)
    destination = output_dir / filename
    if destination.exists():
        return destination, _validate_pdf(destination)

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
        _validate_pdf(temporary_path)
        temporary_path.replace(destination)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return destination, _validate_pdf(destination)


async def _json_response(
    response: httpx.Response,
    model: type[_LenientModel],
) -> _LenientModel:
    response.raise_for_status()
    return model.model_validate_json(response.content)


async def _ingest_papers(
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


async def _cleanup_documents(
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


async def _warmup_embedding(
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


async def _run(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    fixture = json.loads(args.fixture.read_text("utf-8"))
    papers = [Paper.model_validate(item["paper"]) for item in fixture["papers"]]
    if len(papers) != 3:
        raise ValueError("M2 live smoke requires exactly three papers")

    download_timeout = httpx.Timeout(args.download_timeout_seconds, connect=15)
    async with httpx.AsyncClient(
        timeout=download_timeout,
        follow_redirects=True,
        trust_env=False,
    ) as download_client:
        downloads = await asyncio.gather(
            *(
                _download_pdf(download_client, paper=paper, output_dir=args.documents_dir)
                for paper in papers
            )
        )
    document_paths = {
        paper.canonical_paper_id: path for paper, (path, _) in zip(papers, downloads, strict=True)
    }
    total_pdf_bytes = sum(size for _, size in downloads)

    repository = DocuMindBindingRepository(args.output_dir / "bindings.sqlite3")
    documind_timeout = httpx.Timeout(args.ingest_timeout_seconds, connect=15)
    newly_created: list[str] = []
    cleaned_documents = 0
    started = time.perf_counter()
    ingestion_started = time.perf_counter()
    async with (
        httpx.AsyncClient(timeout=documind_timeout, trust_env=False) as documind_http,
        httpx.AsyncClient(
            timeout=httpx.Timeout(args.model_timeout_seconds, connect=15),
            trust_env=False,
        ) as ollama_http,
    ):
        embedding_dimension, embedding_warmup_seconds = await _warmup_embedding(
            ollama_http,
            ollama_url=args.ollama_url,
            model=args.embedding_model,
        )
        client = DocuMindClient(
            client=documind_http,
            base_url=args.documind_url,
            timeout_seconds=args.retrieve_timeout_seconds,
        )
        ready, readiness = await client.retrieval_ready()
        if not ready or readiness is None or readiness.version != "2.2.0":
            raise RuntimeError("DocuMind 2.2.0 retrieval is not ready")
        try:
            _, total_chunks = await _ingest_papers(
                client=documind_http,
                base_url=args.documind_url,
                papers=papers,
                document_paths=document_paths,
                repository=repository,
                newly_created=newly_created,
            )
            ingestion_seconds = time.perf_counter() - ingestion_started
            pipeline = M2EvidencePipeline(
                client=client,
                bindings=repository,
                analyzer=OllamaPaperAnalyzer(
                    client=ollama_http,
                    base_url=args.ollama_url,
                    model=args.model,
                    model_version=args.model_version,
                    timeout_seconds=args.model_timeout_seconds,
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
                capability_id="single-document-dense-retrieval-live-smoke",
            )
        finally:
            if not args.keep_documents and newly_created:
                cleaned_documents = await _cleanup_documents(
                    documind_http,
                    base_url=args.documind_url,
                    document_keys=newly_created,
                )

    evidence = [item for analysis in result.report.analyses for item in analysis.evidence]
    passed = (
        len(result.report.analyses) == 3
        and len(result.retrieval_audits) == 3
        and all(item.service_version == "2.2.0" for item in result.retrieval_audits)
        and all(
            item.status == "succeeded" and item.chunk_count > 0 for item in result.retrieval_audits
        )
        and all(analysis.evidence for analysis in result.report.analyses)
        and (args.keep_documents or cleaned_documents == len(newly_created))
    )
    summary: dict[str, object] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "quality_scope": "Live DocuMind retrieval over versioned public arXiv PDFs.",
        "documind_online_service_used": True,
        "documind_version": "2.2.0",
        "retrieval_schema_version": "1.0",
        "retrieval_version": "dense-v1",
        "source_kind": "versioned_public_arxiv_pdf",
        "embedding_model": args.embedding_model,
        "embedding_dimension": embedding_dimension,
        "embedding_warmup_seconds": round(embedding_warmup_seconds, 3),
        "paper_count": len(result.report.analyses),
        "paper_ids": [item.paper_card.canonical_paper_id for item in result.report.analyses],
        "pdf_bytes": total_pdf_bytes,
        "indexed_chunk_count": total_chunks,
        "retrieved_chunk_count": sum(item.chunk_count for item in result.retrieval_audits),
        "evidence_count": len(evidence),
        "retrieval_attempts": sum(item.attempts for item in result.retrieval_audits),
        "retrieval_duration_seconds": round(
            sum(item.duration_seconds for item in result.retrieval_audits), 3
        ),
        "ingestion_duration_seconds": round(ingestion_seconds, 3),
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
        "end_to_end_duration_seconds": round(time.perf_counter() - started, 3),
        "new_documents": len(newly_created),
        "documents_cleaned_up": cleaned_documents,
        "documents_retained": bool(args.keep_documents),
        "report_content_sha256": result.report.content_sha256,
        "manifest_sha256": persisted.manifest_sha256,
        "billed_api_cost_cny": persisted.manifest.budget.usage.external_cost_cny,
        "passed": passed,
        "notes": [
            "Raw PDFs, retrieved chunks, exact quotes and full reports remain under artifacts/.",
            "Only exact quotes from live DocuMind chunks are accepted.",
            "Independent semantic entailment verification remains deferred to M4.",
            "GPU time is not measured separately from wall time in M2.",
            "The local embedding model is warmed before the bounded readiness check.",
        ],
    }
    write_json(args.summary_output, summary)
    print(json.dumps(summary, ensure_ascii=False))
    return (0 if passed else 1), summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--documents-dir", type=Path, default=DEFAULT_DOCUMENTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--documind-url", default="http://127.0.0.1:8001")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--model-version", default="500a1f067a9f")
    parser.add_argument("--embedding-model", default="qwen3-embedding")
    parser.add_argument("--download-timeout-seconds", type=float, default=120)
    parser.add_argument("--ingest-timeout-seconds", type=float, default=900)
    parser.add_argument("--retrieve-timeout-seconds", type=float, default=60)
    parser.add_argument("--model-timeout-seconds", type=float, default=600)
    parser.add_argument("--keep-documents", action="store_true")
    args = parser.parse_args()
    exit_code, _ = asyncio.run(_run(args))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
