from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import httpx

from scholartrace.contracts import DocuMindBinding, Paper
from scholartrace.evidence.analysis import OllamaPaperAnalyzer
from scholartrace.evidence.bindings import DocuMindBindingRepository
from scholartrace.evidence.client import DocuMindClient
from scholartrace.evidence.models import DocuMindRetrieveResponse
from scholartrace.evidence.pipeline import M2EvidencePipeline

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PLAN = ROOT / "evaluation" / "seeds" / "m6_b3_b4_evidence_sources.json"
M2_FIXTURE = ROOT / "tests" / "fixtures" / "documind" / "m2_three_papers.json"
SPEC = importlib.util.spec_from_file_location(
    "scholartrace_m6_evidence_collection",
    ROOT / "scripts" / "prepare_m6_b3_b4_evidence.py",
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load M6 Evidence collection script")
SCRIPT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SCRIPT
SPEC.loader.exec_module(SCRIPT)
assert isinstance(SCRIPT, ModuleType)
PENDING_QUESTION_IDS = SCRIPT.PENDING_QUESTION_IDS
_load_plan = SCRIPT._load_plan


def test_source_plan_freezes_five_questions_and_nine_new_versioned_papers() -> None:
    plan, papers = _load_plan(SOURCE_PLAN, M2_FIXTURE)

    assert {item.question_id for item in plan.questions} == PENDING_QUESTION_IDS
    assert len(plan.papers) == 9
    assert len(papers) == 12
    assert all(len(item.paper_ids) in {3, 4, 5} for item in plan.questions)
    new_papers = [item.paper for item in plan.papers]
    assert all(paper.access_level == "fulltext" for paper in new_papers)
    assert all(paper.sources[0].source_id.rsplit("v", 1)[-1].isdigit() for paper in new_papers)


def test_multiple_questions_can_share_one_retrieval_before_generation_barrier(
    tmp_path: Path,
) -> None:
    fixture = json.loads(M2_FIXTURE.read_text("utf-8"))
    cases = fixture["papers"]
    papers = [Paper.model_validate(case["paper"]) for case in cases]
    repository = DocuMindBindingRepository(tmp_path / "bindings.sqlite3")
    by_document: dict[str, dict[str, object]] = {}
    by_paper: dict[str, dict[str, object]] = {}
    for case in cases:
        paper = Paper.model_validate(case["paper"])
        binding = DocuMindBinding.model_validate(case["binding"])
        repository.put(binding)
        by_document[binding.document_key] = case
        by_paper[paper.canonical_paper_id] = case

    events: list[str] = []

    def documind_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        events.append("retrieve")
        case = by_document[body["document_key"]]
        response = DocuMindRetrieveResponse.model_validate(case["retrieve_response"])
        return httpx.Response(200, json=response.model_dump(mode="json"), request=request)

    def ollama_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        model_input = json.loads(body["messages"][1]["content"])
        paper_id = model_input["paper"]["canonical_paper_id"]
        events.append("analyze")
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": json.dumps(by_paper[paper_id]["analysis_draft"]),
                },
                "prompt_eval_count": 100,
                "eval_count": 50,
            },
            request=request,
        )

    async def scenario() -> None:
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(documind_handler)) as documind_http,
            httpx.AsyncClient(transport=httpx.MockTransport(ollama_handler)) as ollama_http,
        ):
            pipeline = M2EvidencePipeline(
                client=DocuMindClient(client=documind_http),
                bindings=repository,
                analyzer=OllamaPaperAnalyzer(client=ollama_http, keep_alive="10m"),
                max_concurrency=2,
            )
            batches = await asyncio.gather(
                pipeline.retrieve(papers=papers, question="Question one?"),
                pipeline.retrieve(papers=papers, question="Question two?"),
            )
            assert events == ["retrieve"] * 6
            results = await asyncio.gather(*(pipeline.analyze(batch) for batch in batches))
            assert len(results) == 2

    asyncio.run(scenario())
    assert events[:6] == ["retrieve"] * 6
    assert events[6:] == ["analyze"] * 6
