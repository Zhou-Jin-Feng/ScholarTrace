from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import httpx
import pytest

from scholartrace.contracts import Paper
from scholartrace.evidence import live
from scholartrace.evidence.bindings import DocuMindBindingRepository

PDF = b"%PDF-1.7\nfixture public paper\n"


def _paper(source_id: str = "https://arxiv.org/abs/2401.15884v3") -> Paper:
    return Paper.model_validate(
        {
            "canonical_paper_id": "doi:10.48550/arxiv.2401.15884",
            "title": "Corrective Retrieval Augmented Generation",
            "normalized_title": "corrective retrieval augmented generation",
            "authors": ["Author"],
            "publication_year": 2024,
            "doi": "10.48550/arxiv.2401.15884",
            "arxiv_id": "2401.15884",
            "access_level": "fulltext",
            "sources": [
                {
                    "source": "arxiv",
                    "source_id": source_id,
                    "retrieved_at": "2026-08-29T00:00:00Z",
                    "record_sha256": "a" * 64,
                }
            ],
        }
    )


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_acquire_pdf_records_hash_and_reuses_valid_destination(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url == httpx.URL("https://arxiv.org/pdf/2401.15884v3.pdf")
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf", "Content-Length": str(len(PDF))},
            content=PDF,
        )

    async def scenario() -> tuple[live.PdfAcquisition, live.PdfAcquisition]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            first = await live.acquire_pdf(client, paper=_paper(), output_dir=tmp_path)
            second = await live.acquire_pdf(client, paper=_paper(), output_dir=tmp_path)
            return first, second

    first, second = _run(scenario())
    expected_hash = hashlib.sha256(PDF).hexdigest()
    assert first.sha256 == expected_hash
    assert second.sha256 == expected_hash
    assert first.reused is False
    assert second.reused is True
    assert calls == 1
    assert not list(tmp_path.glob(".*.tmp"))


def test_arxiv_pdf_url_rejects_non_fulltext_access_level() -> None:
    paper = _paper().model_copy(update={"access_level": "abstract"})

    with pytest.raises(live.FullTextAcquisitionError) as raised:
        live.arxiv_pdf_url(paper)

    assert raised.value.code == "access_policy"


def test_acquire_pdf_rejects_content_type_and_cleans_temp_file(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"%PDF-")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(live.FullTextAcquisitionError) as raised:
                await live.acquire_pdf(client, paper=_paper(), output_dir=tmp_path)
            assert raised.value.code == "content_type"

    _run(scenario())
    assert not list(tmp_path.glob(".*.tmp"))
    assert not (tmp_path / "2401.15884v3.pdf").exists()


def test_acquire_pdf_rejects_streaming_size_over_limit(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(live, "MAX_PDF_BYTES", 8)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=PDF)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(live.FullTextAcquisitionError) as raised:
                await live.acquire_pdf(client, paper=_paper(), output_dir=tmp_path)
            assert raised.value.code == "size_limit"

    _run(scenario())
    assert not list(tmp_path.glob(".*.tmp"))


def test_acquire_pdf_rejects_truncated_content_length(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf", "Content-Length": str(len(PDF) + 1)},
            content=PDF,
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(live.FullTextAcquisitionError) as raised:
                await live.acquire_pdf(client, paper=_paper(), output_dir=tmp_path)
            assert raised.value.code == "content_length"

    _run(scenario())
    assert not list(tmp_path.glob(".*.tmp"))


def test_acquire_pdf_rejects_cross_domain_redirect_even_when_client_follows(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "arxiv.org"
        return httpx.Response(302, headers={"Location": "https://example.com/paper.pdf"})

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ) as client:
            with pytest.raises(live.FullTextAcquisitionError) as raised:
                await live.acquire_pdf(client, paper=_paper(), output_dir=tmp_path)
            assert raised.value.code == "redirect_policy"

    _run(scenario())


def test_acquire_pdf_rejects_redirect_loop_after_bounded_hops(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"Location": str(request.url)})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(live.FullTextAcquisitionError) as raised:
                await live.acquire_pdf(client, paper=_paper(), output_dir=tmp_path)
            assert raised.value.code == "redirect_policy"

    _run(scenario())
    assert calls == live.MAX_REDIRECTS + 1
    assert not list(tmp_path.glob(".*.tmp"))


def test_ingest_rejects_provider_hash_mismatch_before_binding(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(PDF)
    paper = _paper()
    document_key = "b" * 64
    index_id = "c" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/documents" and request.method == "GET":
            return httpx.Response(200, json={"items": []})
        if request.url.path == "/api/v1/documents" and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "status": "active",
                    "document_key": document_key,
                    "index_id": index_id,
                    "source_sha256": "d" * 64,
                    "chunk_count": 1,
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(live.FullTextAcquisitionError) as raised:
                await live.ingest_papers(
                    client=client,
                    base_url="http://documind",
                    papers=[paper],
                    document_paths={paper.canonical_paper_id: pdf_path},
                    repository=DocuMindBindingRepository(tmp_path / "bindings.sqlite3"),
                    newly_created=[],
                    expected_source_sha256={
                        paper.canonical_paper_id: hashlib.sha256(PDF).hexdigest()
                    },
                )
            assert raised.value.code == "provider_hash_mismatch"

    _run(scenario())


def test_cleanup_is_idempotent_and_attempts_all_documents() -> None:
    statuses = {"a" * 64: 503, "b" * 64: 404, "c" * 64: 204}

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.rsplit("/", maxsplit=1)[-1]
        return httpx.Response(statuses[key])

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(live.DocumentCleanupError) as raised:
                await live.cleanup_documents(
                    client,
                    base_url="http://documind",
                    document_keys=list(statuses),
                )
            assert raised.value.cleaned_count == 2
            assert raised.value.failed_document_keys == ("a" * 64,)

    _run(scenario())
