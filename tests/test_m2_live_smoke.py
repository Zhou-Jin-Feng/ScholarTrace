from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from scholartrace.contracts import Paper

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "scholartrace_m2_live_smoke",
    ROOT / "scripts" / "run_m2_live_smoke.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load M2 live smoke script")
SCRIPT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SCRIPT
SPEC.loader.exec_module(SCRIPT)


def _script_function(name: str):  # type: ignore[no-untyped-def]
    module = SCRIPT
    assert isinstance(module, ModuleType)
    return getattr(module, name)


_arxiv_pdf_url = _script_function("_arxiv_pdf_url")
_download_pdf = _script_function("_download_pdf")
_validate_pdf = _script_function("_validate_pdf")
_warmup_embedding = _script_function("_warmup_embedding")


def _paper(source_id: str) -> Paper:
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


def test_arxiv_pdf_url_requires_and_preserves_exact_version() -> None:
    url, filename = _arxiv_pdf_url(_paper("http://arxiv.org/abs/2401.15884v3"))

    assert url == "https://arxiv.org/pdf/2401.15884v3"
    assert filename == "2401.15884v3.pdf"


def test_arxiv_pdf_url_uses_identifier_route_without_version() -> None:
    url, filename = _arxiv_pdf_url(_paper("https://arxiv.org/abs/2401.15884"))

    assert url == "https://arxiv.org/pdf/2401.15884"
    assert filename == "2401.15884.pdf"


def test_pdf_validation_checks_magic_and_size(tmp_path: Path) -> None:
    valid = tmp_path / "valid.pdf"
    valid.write_bytes(b"%PDF-1.7\ncontent")
    invalid = tmp_path / "invalid.pdf"
    invalid.write_bytes(b"not-a-pdf")

    assert _validate_pdf(valid) == valid.stat().st_size
    with pytest.raises(ValueError, match="not a PDF"):
        _validate_pdf(invalid)


def test_download_rejects_redirect_outside_arxiv(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "arxiv.org":
            return httpx.Response(302, headers={"Location": "https://example.com/paper.pdf"})
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7\ncontent",
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        ) as client:
            with pytest.raises(ValueError, match="redirect left the allowlist"):
                await _download_pdf(
                    client, paper=_paper("http://arxiv.org/abs/2401.15884v3"), output_dir=tmp_path
                )

    asyncio.run(scenario())


def test_embedding_warmup_keeps_installed_model_resident() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload == {
            "model": "qwen3-embedding",
            "input": "scholartrace m2 readiness",
            "keep_alive": "10m",
        }
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})

    async def scenario() -> tuple[int, float]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _warmup_embedding(
                client,
                ollama_url="http://127.0.0.1:11434",
                model="qwen3-embedding",
            )

    dimension, duration = asyncio.run(scenario())
    assert dimension == 3
    assert duration >= 0
