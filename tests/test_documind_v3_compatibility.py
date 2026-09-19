from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from scholartrace.contracts import DocuMindBinding, Paper
from scholartrace.evidence import live
from scholartrace.evidence.bindings import DocuMindBindingRepository
from scholartrace.evidence.client import DocuMindClient, DocuMindProtocolError, EvidenceScopeError
from scholartrace.evidence.models import DocuMindRetrieveResponse

ROOT = Path(__file__).resolve().parents[1]
PROVIDER = json.loads((ROOT / "tests/fixtures/documind/v3_0_0_provider.json").read_text("utf-8"))
PDF = b"%PDF-1.7\nSynthetic local compatibility test only.\n"
SOURCE_HASH = hashlib.sha256(PDF).hexdigest()


def payload(version="3.0.0"):
    content = "Synthetic evidence."
    return {
        "schema_version": "1.0",
        "service_version": version,
        "retrieval_version": "dense-v1",
        "retrieval_mode": "dense",
        "document_key": "a" * 64,
        "index_id": "b" * 64,
        "source_sha256": SOURCE_HASH,
        "chunks": [
            {
                "chunk_id": "d" * 64,
                "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "source": "synthetic.pdf",
                "page_number": 1,
                "distance": 0.1,
                "rank": 1,
            }
        ],
    }


def binding(version="3.0.0"):
    return DocuMindBinding(
        canonical_paper_id="doi:10.1000/synthetic",
        document_key="a" * 64,
        index_id="b" * 64,
        source_sha256=SOURCE_HASH,
        documind_version=version,
        retrieval_schema_version="1.0",
    )


def ready(version="3.0.0", component="ready", degraded=False):
    return {
        "status": "degraded" if degraded else "ready",
        "version": version,
        "ready": not degraded,
        "components": {
            "application": "ready",
            "milvus": "ready",
            "embedding": "ready",
            "llm": "unavailable" if degraded else "ready",
            "registry": "ready",
            "retrieval": component,
        },
        "error_type": "dependency_unavailable" if degraded else None,
    }


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("version", ["2.1.0", "2.2.0", "2.12.3", "3.0.0"])
def test_supported_versions_retrieve_and_schema(version):
    body = payload(version)
    Draft202012Validator(PROVIDER["models"]["RetrieveResponse"]).validate(body)
    assert DocuMindRetrieveResponse.model_validate(body).service_version == version

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
        ) as http:
            result = await DocuMindClient(client=http).retrieve(
                canonical_paper_id=binding(version).canonical_paper_id,
                binding=binding(version),
                query="Synthetic query",
            )
        assert result.audit.service_version == version
        assert result.audit.status == "succeeded"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "version",
    [
        "2.0.9",
        "3.0.1",
        "3.1.0",
        "4.0.0",
        "3.0.0-beta",
        "3.0.0+build",
        "v3.0.0",
        "3.0.0\n",
    ],
)
def test_other_versions_still_rejected(version):
    with pytest.raises(ValidationError):
        DocuMindRetrieveResponse.model_validate(payload(version))
    assert DocuMindClient._supports_retrieve(version) is False


@pytest.mark.parametrize(
    ("http_status", "component", "expected"),
    [
        (200, "ready", True),
        (503, "ready", True),
        (503, "unavailable", False),
        (200, "unavailable", False),
        (401, "ready", False),
        (500, "ready", False),
    ],
)
def test_v3_readiness_honors_component_and_http_contract(http_status, component, expected):
    body = ready(component=component, degraded=http_status == 503)
    Draft202012Validator(PROVIDER["models"]["HealthResponse"]).validate(body)

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(http_status, json=body))
        ) as http:
            actual, _ = await DocuMindClient(client=http).retrieval_ready()
            assert actual is expected

    asyncio.run(scenario())


def test_v3_readiness_requires_component_even_if_ready_flag_true():
    body = {"status": "ready", "version": "3.0.0", "ready": True}

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
        ) as http:
            actual, _ = await DocuMindClient(client=http).retrieval_ready()
            assert actual is False

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "mutation",
    [
        "document",
        "index",
        "source",
        "version",
        "schema",
        "hash",
        "rank",
        "duplicate",
        "unknown",
    ],
)
def test_v3_does_not_relax_evidence_integrity(mutation):
    body = payload()
    if mutation in {"document", "index", "source"}:
        body[
            {"document": "document_key", "index": "index_id", "source": "source_sha256"}[mutation]
        ] = "f" * 64
    elif mutation == "version":
        body["service_version"] = "2.2.0"
    elif mutation == "schema":
        body["schema_version"] = "2.0"
    elif mutation == "hash":
        body["chunks"][0]["content_sha256"] = "e" * 64
    elif mutation == "rank":
        body["chunks"][0]["rank"] = 2
    elif mutation == "duplicate":
        body["chunks"].append({**body["chunks"][0], "rank": 2})
    else:
        body["debug"] = "not permitted"
    calls = []

    async def scenario():
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises((DocuMindProtocolError, EvidenceScopeError)):
                await DocuMindClient(client=http).retrieve(
                    canonical_paper_id=binding().canonical_paper_id,
                    binding=binding(),
                    query="query",
                )

    asyncio.run(scenario())
    assert len(calls) == 1


def document_payloads():
    summary = {
        "document_key": "a" * 64,
        "display_name": "synthetic.pdf",
        "status": "active",
        "chunk_count": 1,
        "active_index_id": "b" * 64,
        "version_count": 1,
        "created_at": "2026-09-16T00:00:00Z",
        "updated_at": "2026-09-16T00:00:00Z",
    }
    return {
        "list": {"items": [], "total": 0},
        "upload": {
            "status": "indexed",
            "operation_id": "synthetic",
            "document_key": "a" * 64,
            "document_version_id": "c" * 64,
            "index_id": "b" * 64,
            "source_sha256": SOURCE_HASH,
            "chunk_count": 1,
            "collection_count": 1,
            "cleanup_pending": False,
        },
        "detail": {**summary, "indexes": []},
        "delete": {
            "status": "deleted",
            "document_key": "a" * 64,
            "deleted_index_count": 1,
            "deleted_chunk_count": 1,
            "collection_count": 0,
            "cleanup_pending": False,
        },
    }


def test_v3_upload_binding_retrieve_cleanup_contract_flow(tmp_path):
    paper = Paper.model_validate(
        json.loads((ROOT / "tests/fixtures/documind/m2_three_papers.json").read_text("utf-8"))[
            "papers"
        ][0]["paper"]
    )
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(PDF)
    repo = DocuMindBindingRepository(tmp_path / "bindings.sqlite")
    newly_created = []
    bodies = document_payloads()
    for label, model in [
        ("list", "DocumentListResponse"),
        ("upload", "IngestionResponse"),
        ("detail", "DocumentDetailResponse"),
        ("delete", "DocumentDeletionResponse"),
    ]:
        Draft202012Validator(PROVIDER["models"][model]).validate(bodies[label])
    calls = []

    def handler(request):
        key = (request.method, request.url.path)
        calls.append(key)
        if key == ("GET", "/api/v1/health/ready"):
            return httpx.Response(200, json=ready())
        if key == ("GET", "/api/v1/documents"):
            return httpx.Response(200, json=bodies["list"])
        if key == ("POST", "/api/v1/documents"):
            assert 'name="file"' in request.content.decode()
            assert PDF in request.content
            return httpx.Response(200, json=bodies["upload"])
        if key == ("GET", "/api/v1/documents/" + "a" * 64):
            return httpx.Response(200, json=bodies["detail"])
        if key == ("POST", "/api/v1/retrieve"):
            req = json.loads(request.content)
            Draft202012Validator(PROVIDER["models"]["RetrieveRequest"]).validate(req)
            return httpx.Response(200, json=payload())
        if key == ("DELETE", "/api/v1/documents/" + "a" * 64):
            return httpx.Response(200, json=bodies["delete"])
        raise AssertionError(key)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = DocuMindClient(client=http, base_url="http://provider")
            is_ready, status = await client.retrieval_ready()
            assert is_ready and status.version == "3.0.0"
            bindings, count = await live.ingest_papers(
                client=http,
                base_url="http://provider",
                papers=[paper],
                document_paths={paper.canonical_paper_id: pdf},
                repository=repo,
                newly_created=newly_created,
                documind_version=status.version,
                expected_source_sha256={paper.canonical_paper_id: SOURCE_HASH},
            )
            assert count == 1 and bindings[0].documind_version == "3.0.0"
            assert repo.get(paper.canonical_paper_id) == bindings[0]
            result = await client.retrieve(
                canonical_paper_id=paper.canonical_paper_id,
                binding=bindings[0],
                query="Synthetic query",
            )
            assert result.audit.service_version == "3.0.0"
            assert (
                await live.cleanup_documents(
                    http, base_url="http://provider", document_keys=newly_created
                )
                == 1
            )

    asyncio.run(scenario())
    assert len(calls) == 6


@pytest.mark.parametrize("mutation", ["pending", "wrong-key", "wrong-status", "malformed"])
def test_cleanup_does_not_count_incomplete_or_unrelated_delete_as_success(mutation):
    body = copy.deepcopy(document_payloads()["delete"])
    if mutation == "pending":
        body["cleanup_pending"] = True
    elif mutation == "wrong-key":
        body["document_key"] = "f" * 64
    elif mutation == "wrong-status":
        body["status"] = "deleting"
    else:
        body = {}

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))
        ) as http:
            with pytest.raises(live.DocumentCleanupError) as caught:
                await live.cleanup_documents(
                    http, base_url="http://provider", document_keys=["a" * 64]
                )
            assert caught.value.cleaned_count == 0

    asyncio.run(scenario())


def test_verifier_readiness_agrees_with_client_on_retrieval_only_503(monkeypatch):
    module = script("verify_m2_documind_compatibility")
    original = httpx.Client

    def client(**kwargs):
        return original(
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(503, json=ready(degraded=True))
            ),
        )

    monkeypatch.setattr(module.httpx, "Client", client)
    assert module._online_readiness("http://provider", 5, {"3.0.0"})["compatible"] is True


def test_live_cli_accepts_exact_v3_identity():
    module = script("run_m2_live_smoke")
    module._validate_provider_identity("3.0.0", PROVIDER["git_commit"])


def test_illustrative_provider_hash_is_not_accepted_as_evidence():
    sample = PROVIDER["models"]["RetrieveResponse"]["examples"][0]
    Draft202012Validator(PROVIDER["models"]["RetrieveResponse"]).validate(sample)
    with pytest.raises(ValidationError, match="content hash mismatch"):
        DocuMindRetrieveResponse.model_validate(sample)


@pytest.mark.parametrize("version", ["2.2.0", "3.0.0"])
def test_live_smoke_script_preserves_actual_identity_through_manifest(
    tmp_path, monkeypatch, version
):
    from argparse import Namespace

    module = script("run_m2_live_smoke")
    fixture = json.loads((ROOT / "tests/fixtures/documind/m2_three_papers.json").read_text("utf-8"))
    # Fixed local runner identity; this scenario tests Provider provenance, not Git discovery.
    monkeypatch.setattr(module, "_git", lambda *args: "a" * 40 if args[0] == "rev-parse" else "")
    cases = fixture["papers"]
    by_key = {case["binding"]["document_key"]: case for case in cases}
    deleted = []
    by_paper = {case["paper"]["canonical_paper_id"]: case for case in cases}

    def handler(request):
        path = request.url.path
        if request.url.host in {"arxiv.org", "export.arxiv.org"}:
            return httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=PDF)
        if path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})
        if path == "/api/chat":
            body = json.loads(request.content)
            model_input = json.loads(body["messages"][1]["content"])
            case = by_paper[model_input["paper"]["canonical_paper_id"]]
            return httpx.Response(
                200,
                json={
                    "message": {"role": "assistant", "content": json.dumps(case["analysis_draft"])},
                    "prompt_eval_count": 100,
                    "eval_count": 50,
                },
            )
        if path == "/api/v1/health/ready":
            return httpx.Response(200, json=ready(version))
        if path == "/api/v1/documents" and request.method == "GET":
            return httpx.Response(200, json={"items": [], "total": 0})
        if path == "/api/v1/documents" and request.method == "POST":
            body = request.content.decode()
            case = next(c for c in cases if c["paper"]["arxiv_id"] in body)
            b = case["binding"]
            data = {
                **document_payloads()["upload"],
                "document_key": b["document_key"],
                "index_id": b["index_id"],
            }
            return httpx.Response(200, json=data)
        if path.startswith("/api/v1/documents/"):
            key = path.rsplit("/", 1)[1]
            b = by_key[key]["binding"]
            if request.method == "DELETE":
                deleted.append(key)
                return httpx.Response(
                    200, json={**document_payloads()["delete"], "document_key": key}
                )
            return httpx.Response(
                200,
                json={
                    **document_payloads()["detail"],
                    "document_key": key,
                    "active_index_id": b["index_id"],
                },
            )
        if path == "/api/v1/retrieve":
            case = by_key[json.loads(request.content)["document_key"]]
            return httpx.Response(
                200,
                json={
                    **case["retrieve_response"],
                    "service_version": version,
                    "source_sha256": SOURCE_HASH,
                },
            )
        raise AssertionError((request.method, str(request.url)))

    original = httpx.AsyncClient
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kw: original(**kw, transport=httpx.MockTransport(handler)),
    )
    args = Namespace(
        fixture=ROOT / "tests/fixtures/documind/m2_three_papers.json",
        documents_dir=tmp_path / "pdfs",
        output_dir=tmp_path / "output",
        summary_output=tmp_path / "summary.json",
        documind_version=version,
        documind_commit=PROVIDER["git_commit"] if version == "3.0.0" else "f" * 40,
        documind_url="http://provider",
        ollama_url="http://ollama",
        model="qwen3:8b",
        model_version="fixture",
        embedding_model="qwen3-embedding",
        download_timeout_seconds=5,
        ingest_timeout_seconds=5,
        model_timeout_seconds=5,
        retrieve_timeout_seconds=5,
        keep_documents=False,
    )
    exit_code, summary = asyncio.run(module._run(args))
    assert exit_code == 0 and summary["passed"] is True
    assert len(deleted) == 3
    assert summary["documind_version"] == version
    manifest = json.loads((args.output_dir / "run_manifest.json").read_text("utf-8"))
    provider = next(b for b in manifest["service_baselines"] if b["service"] == "documind")
    assert provider["version"] == version and provider["git_commit"] == args.documind_commit
    report = json.loads((args.output_dir / "evidence_report.json").read_text("utf-8"))
    assert all(a["retrieval_audit"]["service_version"] == version for a in report["analyses"])


def test_existing_binding_is_not_silently_upgraded(tmp_path):
    repo = DocuMindBindingRepository(tmp_path / "bindings.sqlite")
    repo.put(binding("2.2.0"))

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload()))
        ) as http:
            with pytest.raises(EvidenceScopeError, match="version differs"):
                await DocuMindClient(client=http).retrieve(
                    canonical_paper_id=binding().canonical_paper_id,
                    binding=repo.get(binding().canonical_paper_id),
                    query="query",
                )

    asyncio.run(scenario())
    assert repo.get(binding().canonical_paper_id).documind_version == "2.2.0"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "indexing"),
        ("active_index_id", "f" * 64),
        ("document_key", "f" * 64),
        ("chunk_count", 2),
    ],
)
def test_v3_ingestion_rejects_inconsistent_detail_before_binding(tmp_path, field, value):
    paper = Paper.model_validate(
        json.loads((ROOT / "tests/fixtures/documind/m2_three_papers.json").read_text("utf-8"))[
            "papers"
        ][0]["paper"]
    )
    pdf = tmp_path / "synthetic.pdf"
    pdf.write_bytes(PDF)
    repo = DocuMindBindingRepository(tmp_path / "binding.sqlite")
    bodies = document_payloads()
    bodies["detail"][field] = value

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json=bodies["upload"])
        label = "list" if request.url.path.endswith("/documents") else "detail"
        return httpx.Response(200, json=bodies[label])

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(ValueError, match="not active"):
                await live.ingest_papers(
                    client=http,
                    base_url="http://provider",
                    papers=[paper],
                    document_paths={paper.canonical_paper_id: pdf},
                    repository=repo,
                    newly_created=[],
                    documind_version="3.0.0",
                )

    asyncio.run(scenario())
    assert repo.get(paper.canonical_paper_id) is None


@pytest.mark.parametrize(
    ("http_status", "code", "expected_calls"),
    [
        (409, "stale_document_index", 1),
        (404, "document_not_found", 1),
        (503, "retrieval_capacity_exceeded", 2),
        (503, "retrieval_timeout", 2),
    ],
)
def test_v3_error_policy_remains_bounded(http_status, code, expected_calls):
    from scholartrace.evidence.client import DocuMindClientError

    calls = []

    async def no_wait(seconds):
        pass

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            http_status,
            json={
                "error": {
                    "code": code,
                    "message": "Synthetic error",
                    "request_id": "test",
                }
            },
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(DocuMindClientError) as caught:
                await DocuMindClient(client=http, sleeper=no_wait).retrieve(
                    canonical_paper_id=binding().canonical_paper_id,
                    binding=binding(),
                    query="query",
                )
            assert caught.value.code == code

    asyncio.run(scenario())
    assert len(calls) == expected_calls
    assert all(c == calls[0] for c in calls)


def test_v3_ingest_rejects_unsupported_version_before_http(tmp_path):
    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: pytest.fail("unexpected HTTP"))
        ) as http:
            with pytest.raises(ValueError, match="unsupported"):
                await live.ingest_papers(
                    client=http,
                    base_url="http://provider",
                    papers=[],
                    document_paths={},
                    repository=DocuMindBindingRepository(tmp_path / "bindings.sqlite"),
                    newly_created=[],
                    documind_version="3.0.1",
                )

    asyncio.run(scenario())
