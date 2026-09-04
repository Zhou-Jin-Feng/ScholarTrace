"""Collect and freeze real DocuMind Evidence for the remaining M6 questions."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from scholartrace.contracts import ModelUsageRecord, Paper, StableId
from scholartrace.evidence.analysis import (
    PAPER_ANALYSIS_SYSTEM_PROMPT,
    OllamaPaperAnalyzer,
)
from scholartrace.evidence.bindings import DocuMindBindingRepository
from scholartrace.evidence.client import DocuMindClient
from scholartrace.evidence.live import (
    FullTextAcquisitionError,
    cleanup_documents,
    download_pdf,
    ingest_papers,
    warmup_embedding,
)
from scholartrace.evidence.models import EvidenceReportArtifact, RetrievalAudit
from scholartrace.evidence.pipeline import M2EvidencePipeline
from scholartrace.evidence.report import build_evidence_report
from scholartrace.scholargraph.evaluation import load_question_sets
from scholartrace.scholargraph.pilot_input import load_m2_pilot_artifacts
from scholartrace.search.storage import write_json, write_text

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_PLAN = ROOT / "evaluation" / "seeds" / "m6_b3_b4_evidence_sources.json"
DEFAULT_EXISTING_FIXTURE = ROOT / "tests" / "fixtures" / "documind" / "m2_three_papers.json"
DEFAULT_ELIGIBLE = ROOT / "evaluation" / "seeds" / "m5_scholargraph_eligible_eval.jsonl"
DEFAULT_BOUNDARY = ROOT / "evaluation" / "seeds" / "m5_scholargraph_boundary_eval.jsonl"
DEFAULT_DOCUMENTS = ROOT / "artifacts" / "m6-b3-b4-documents"
DEFAULT_OUTPUT = ROOT / "artifacts" / "m6-b3-b4-evidence"
DEFAULT_SUMMARY = ROOT / "evaluation" / "reports" / "m6_b3_b4_evidence_coverage.json"
PENDING_QUESTION_IDS = frozenset(
    {
        "sg-eligible-01",
        "sg-eligible-03",
        "sg-eligible-04",
        "sg-eligible-05",
        "sg-eligible-06",
    }
)
M6_ANALYSIS_SYSTEM_PROMPT = (
    PAPER_ANALYSIS_SYSTEM_PROMPT
    + " Every numeric token in a Claim must appear verbatim in the cited quote; otherwise "
    "omit that number or omit the Claim."
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceSourceQuestion(StrictModel):
    question_id: StableId
    question: str = Field(min_length=2, max_length=4000)
    paper_ids: list[StableId] = Field(min_length=3, max_length=5)


class EvidenceSourcePaper(StrictModel):
    paper: Paper


class EvidenceSourcePlan(StrictModel):
    schema_version: str
    purpose: str
    retrieved_at: datetime
    existing_fixture: str
    questions: list[EvidenceSourceQuestion]
    papers: list[EvidenceSourcePaper]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-plan", type=Path, default=DEFAULT_SOURCE_PLAN)
    parser.add_argument("--existing-fixture", type=Path, default=DEFAULT_EXISTING_FIXTURE)
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
    parser.add_argument("--retrieve-timeout-seconds", type=float, default=90)
    parser.add_argument("--model-timeout-seconds", type=float, default=600)
    parser.add_argument("--keep-documents", action="store_true")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def _load_plan(
    source_path: Path,
    existing_fixture_path: Path,
) -> tuple[EvidenceSourcePlan, dict[str, Paper]]:
    plan = EvidenceSourcePlan.model_validate_json(source_path.read_bytes())
    if plan.schema_version != "1.0":
        raise ValueError("M6 Evidence source plan schema must be 1.0")
    if Path(plan.existing_fixture).as_posix() != "tests/fixtures/documind/m2_three_papers.json":
        raise ValueError("M6 Evidence source plan changed its existing M2 fixture")
    if {item.question_id for item in plan.questions} != PENDING_QUESTION_IDS:
        raise ValueError("M6 Evidence source plan does not cover the five pending questions")

    frozen_questions = load_question_sets(DEFAULT_ELIGIBLE, DEFAULT_BOUNDARY)
    for item in plan.questions:
        frozen = frozen_questions.get(item.question_id)
        if frozen is None or frozen.subset != "scholargraph_eligible_eval":
            raise ValueError(f"unknown M6 eligible question: {item.question_id}")
        if frozen.question != item.question:
            raise ValueError(f"M6 question text drifted: {item.question_id}")
        if len(item.paper_ids) != len(set(item.paper_ids)):
            raise ValueError(f"M6 question has duplicate papers: {item.question_id}")

    existing_payload = json.loads(existing_fixture_path.read_text("utf-8"))
    existing_papers = [Paper.model_validate(row["paper"]) for row in existing_payload["papers"]]
    papers = existing_papers + [row.paper for row in plan.papers]
    by_id = {paper.canonical_paper_id: paper for paper in papers}
    if len(by_id) != len(papers):
        raise ValueError("M6 Evidence source plan contains duplicate paper identities")
    required_ids = {paper_id for item in plan.questions for paper_id in item.paper_ids}
    missing = required_ids - set(by_id)
    if missing:
        raise ValueError("M6 Evidence source plan references missing papers")
    return plan, by_id


def _write_private_question_artifacts(
    *,
    output_dir: Path,
    question: EvidenceSourceQuestion,
    papers: list[Paper],
    result: object,
) -> tuple[Path, Path]:
    from scholartrace.evidence.pipeline import EvidencePipelineResult

    if not isinstance(result, EvidencePipelineResult):
        raise TypeError("M6 Evidence collection returned an invalid pipeline result")
    question_dir = output_dir / question.question_id
    report_path = question_dir / "evidence_report.json"
    fixture_path = question_dir / "paper_fixture.json"
    write_json(report_path, result.report.model_dump(mode="json"))
    write_text(question_dir / "evidence_report.md", result.report.content_markdown)
    write_json(
        question_dir / "retrieval_audits.json",
        [item.model_dump(mode="json") for item in result.retrieval_audits],
    )
    write_json(
        question_dir / "model_usage.json",
        [item.model_dump(mode="json") for item in result.model_usage],
    )
    write_json(
        fixture_path,
        {
            "schema_version": "1.0",
            "fixture_kind": "m6-private-live-documind-evidence",
            "question_id": question.question_id,
            "question": question.question,
            "papers": [{"paper": paper.model_dump(mode="json")} for paper in papers],
        },
    )
    return report_path, fixture_path


def _repair_numeric_mismatches(question_dir: Path) -> int:
    report_path = question_dir / "evidence_report.json"
    if not report_path.is_file():
        return 0
    original_bytes = report_path.read_bytes()
    report = EvidenceReportArtifact.model_validate_json(original_bytes)
    repaired_analyses = []
    removed_claim_ids: list[str] = []
    for analysis in report.analyses:
        evidence_by_id = {item.evidence_id: item for item in analysis.evidence}
        kept_claims = []
        for claim in analysis.claims:
            referenced = list(dict.fromkeys(claim.evidence_ids + claim.counter_evidence_ids))
            quotes = " ".join(
                evidence_by_id[evidence_id].quote
                for evidence_id in referenced
                if evidence_id in evidence_by_id
            )
            if OllamaPaperAnalyzer._numbers(claim.text) - OllamaPaperAnalyzer._numbers(quotes):
                removed_claim_ids.append(claim.claim_id)
            else:
                kept_claims.append(claim)
        if not kept_claims:
            raise ValueError(
                "numeric filtering would remove every Claim from one M6 paper analysis"
            )
        kept_evidence_ids = {
            evidence_id
            for claim in kept_claims
            for evidence_id in claim.evidence_ids + claim.counter_evidence_ids
        }
        kept_evidence = [
            item for item in analysis.evidence if item.evidence_id in kept_evidence_ids
        ]
        repaired_card = analysis.paper_card.model_copy(
            update={
                "claim_ids": [claim.claim_id for claim in kept_claims],
                "evidence_ids": [item.evidence_id for item in kept_evidence],
            }
        )
        repaired_analyses.append(
            analysis.model_copy(
                update={
                    "paper_card": repaired_card,
                    "claims": kept_claims,
                    "evidence": kept_evidence,
                }
            )
        )
    if not removed_claim_ids:
        return 0

    repaired = build_evidence_report(
        question=report.question,
        analyses=repaired_analyses,
        generated_at=report.generated_at,
    )
    backup_path = question_dir / "evidence_report.pre_numeric_filter.json"
    if not backup_path.exists():
        write_json(backup_path, report.model_dump(mode="json"))
    write_json(report_path, repaired.model_dump(mode="json"))
    write_json(
        question_dir / "numeric_filter_audit.json",
        {
            "schema_version": "1.0",
            "purpose": "m6-private-deterministic-numeric-claim-filter",
            "original_report_sha256": hashlib.sha256(original_bytes).hexdigest(),
            "repaired_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "removed_claim_ids": sorted(removed_claim_ids),
            "removed_claim_count": len(removed_claim_ids),
            "rule": "Every numeric token in a Claim must occur in its referenced quote.",
            "applied_at": datetime.now(UTC).isoformat(),
        },
    )
    return len(removed_claim_ids)


def _summarize_questions(
    plan: EvidenceSourcePlan,
    output_dir: Path,
) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for question in plan.questions:
        question_dir = output_dir / question.question_id
        packet = load_m2_pilot_artifacts(
            report_path=question_dir / "evidence_report.json",
            paper_fixture_path=question_dir / "paper_fixture.json",
        )
        audits = TypeAdapter(list[RetrievalAudit]).validate_json(
            (question_dir / "retrieval_audits.json").read_bytes()
        )
        usage = TypeAdapter(list[ModelUsageRecord]).validate_json(
            (question_dir / "model_usage.json").read_bytes()
        )
        summaries.append(
            {
                "question_id": question.question_id,
                "paper_ids": sorted(item.canonical_paper_id for item in packet.papers),
                "paper_count": len(packet.papers),
                "claim_count": len(packet.claims),
                "evidence_count": len(packet.evidence),
                "retrieval_calls": sum(item.attempts for item in audits),
                "local_model_calls": sum(item.call_count for item in usage),
                "local_input_tokens": sum(item.input_tokens for item in usage),
                "local_output_tokens": sum(item.output_tokens for item in usage),
                "deterministic_validation_outcome": packet.validation.outcome,
                "paper_pool_sha256": packet.paper_pool_sha256,
                "source_report_sha256": packet.source_report_sha256,
            }
        )
    return summaries


async def run(args: argparse.Namespace) -> dict[str, object]:
    if args.output_dir.exists() and not args.resume:
        raise ValueError("M6 Evidence output already exists; use --resume after inspection")
    plan, papers_by_id = _load_plan(args.source_plan, args.existing_fixture)
    questions_to_run = list(plan.questions)
    numeric_claims_filtered = 0
    if args.resume:
        questions_to_run = []
        for question in plan.questions:
            question_dir = args.output_dir / question.question_id
            try:
                numeric_claims_filtered += _repair_numeric_mismatches(question_dir)
                load_m2_pilot_artifacts(
                    report_path=question_dir / "evidence_report.json",
                    paper_fixture_path=question_dir / "paper_fixture.json",
                )
            except (OSError, ValueError):
                questions_to_run.append(question)
    if not questions_to_run:
        question_summaries = _summarize_questions(plan, args.output_dir)
        summary: dict[str, object] = {
            "schema_version": "1.0",
            "generated_at": datetime.now(UTC).isoformat(),
            "quality_scope": "Real DocuMind full-text Evidence coverage; no paid calls.",
            "source_plan_sha256": hashlib.sha256(args.source_plan.read_bytes()).hexdigest(),
            "source_kind": "versioned_public_arxiv_pdf",
            "documind_version": "2.2.0",
            "retrieval_schema_version": "1.0",
            "embedding_model": args.embedding_model,
            "embedding_dimension": 4096,
            "embedding_warmup_seconds_this_run": 0,
            "local_analysis_model": args.model,
            "local_analysis_prompt_sha256": hashlib.sha256(
                M6_ANALYSIS_SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
            "unique_paper_count": len(
                {paper_id for row in plan.questions for paper_id in row.paper_ids}
            ),
            "indexed_chunk_count_this_run": 0,
            "questions_collected_this_run": [],
            "question_count": len(question_summaries),
            "retrieval_phase_completed_before_generation": True,
            "retrieval_barrier_at_this_run": None,
            "numeric_claims_filtered_this_run": numeric_claims_filtered,
            "new_documents": 0,
            "documents_cleaned_up": 0,
            "documents_retained": False,
            "duration_seconds": 0,
            "questions": question_summaries,
            "paid_provider_calls": 0,
            "raw_evidence_stored_publicly": False,
            "passed": (
                len(question_summaries) == 5
                and all(
                    item["deterministic_validation_outcome"] == "succeeded"
                    for item in question_summaries
                )
            ),
        }
        write_json(args.summary_output, summary)
        return summary

    selected_ids = sorted(
        {paper_id for row in questions_to_run for paper_id in row.paper_ids}
    )
    selected_papers = [papers_by_id[paper_id] for paper_id in selected_ids]

    download_timeout = httpx.Timeout(args.download_timeout_seconds, connect=15)
    async with httpx.AsyncClient(
        timeout=download_timeout,
        follow_redirects=False,
        trust_env=True,
    ) as download_client:
        download_semaphore = asyncio.Semaphore(3)

        async def download_with_retry(paper: Paper) -> tuple[Path, int]:
            last_error: FullTextAcquisitionError | None = None
            async with download_semaphore:
                for attempt in range(1, 4):
                    try:
                        return await download_pdf(
                            download_client,
                            paper=paper,
                            output_dir=args.documents_dir,
                        )
                    except FullTextAcquisitionError as exc:
                        last_error = exc
                        if exc.code not in {"transport_error", "http_error"}:
                            raise
                        if attempt < 3:
                            await asyncio.sleep(attempt)
            raise RuntimeError(
                f"arXiv download failed after three attempts: {paper.canonical_paper_id}"
            ) from last_error

        downloads = await asyncio.gather(
            *(download_with_retry(paper) for paper in selected_papers)
        )
    document_paths = {
        paper.canonical_paper_id: path
        for paper, (path, _) in zip(selected_papers, downloads, strict=True)
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    repository = DocuMindBindingRepository(args.output_dir / "bindings.sqlite3")
    newly_created: list[str] = []
    cleaned_documents = 0
    started = time.perf_counter()
    documind_timeout = httpx.Timeout(args.ingest_timeout_seconds, connect=15)
    async with (
        httpx.AsyncClient(timeout=documind_timeout, trust_env=False) as documind_http,
        httpx.AsyncClient(
            timeout=httpx.Timeout(args.model_timeout_seconds, connect=15),
            trust_env=False,
        ) as ollama_http,
    ):
        embedding_dimension, warmup_seconds = await warmup_embedding(
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
            raise RuntimeError("DocuMind 2.2.0 retrieval is not ready after warmup")
        try:
            _, total_chunks = await ingest_papers(
                client=documind_http,
                base_url=args.documind_url,
                papers=selected_papers,
                document_paths=document_paths,
                repository=repository,
                newly_created=newly_created,
            )
            pipeline = M2EvidencePipeline(
                client=client,
                bindings=repository,
                analyzer=OllamaPaperAnalyzer(
                    client=ollama_http,
                    base_url=args.ollama_url,
                    model=args.model,
                    model_version=args.model_version,
                    timeout_seconds=args.model_timeout_seconds,
                    keep_alive="10m",
                    system_prompt=M6_ANALYSIS_SYSTEM_PROMPT,
                ),
                max_concurrency=2,
            )
            question_papers = {
                item.question_id: [papers_by_id[paper_id] for paper_id in item.paper_ids]
                for item in questions_to_run
            }
            batches = await asyncio.gather(
                *(
                    pipeline.retrieve(
                        papers=question_papers[item.question_id],
                        question=item.question,
                    )
                    for item in questions_to_run
                )
            )
            retrieval_barrier_at = datetime.now(UTC)
            results = await asyncio.gather(*(pipeline.analyze(batch) for batch in batches))
        finally:
            if not args.keep_documents and newly_created:
                cleaned_documents = await cleanup_documents(
                    documind_http,
                    base_url=args.documind_url,
                    document_keys=newly_created,
                )

    for question, papers, result in zip(
        questions_to_run,
        (question_papers[item.question_id] for item in questions_to_run),
        results,
        strict=True,
    ):
        _write_private_question_artifacts(
            output_dir=args.output_dir,
            question=question,
            papers=papers,
            result=result,
        )

    question_summaries = _summarize_questions(plan, args.output_dir)

    source_plan_sha = hashlib.sha256(args.source_plan.read_bytes()).hexdigest()
    summary: dict[str, object] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "quality_scope": "Real DocuMind full-text Evidence coverage; no paid provider calls.",
        "source_plan_sha256": source_plan_sha,
        "source_kind": "versioned_public_arxiv_pdf",
        "documind_version": "2.2.0",
        "retrieval_schema_version": "1.0",
        "embedding_model": args.embedding_model,
        "embedding_dimension": embedding_dimension,
        "embedding_warmup_seconds": round(warmup_seconds, 3),
        "local_analysis_model": args.model,
        "local_analysis_prompt_sha256": hashlib.sha256(
            M6_ANALYSIS_SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest(),
        "unique_paper_count": len(
            {paper_id for row in plan.questions for paper_id in row.paper_ids}
        ),
        "indexed_chunk_count_this_run": total_chunks,
        "questions_collected_this_run": [item.question_id for item in questions_to_run],
        "question_count": len(question_summaries),
        "retrieval_phase_completed_before_generation": True,
        "retrieval_barrier_at": retrieval_barrier_at.isoformat(),
        "numeric_claims_filtered_this_run": numeric_claims_filtered,
        "new_documents": len(newly_created),
        "documents_cleaned_up": cleaned_documents,
        "documents_retained": bool(args.keep_documents),
        "duration_seconds": round(time.perf_counter() - started, 3),
        "questions": question_summaries,
        "paid_provider_calls": 0,
        "raw_evidence_stored_publicly": False,
        "passed": (
            len(question_summaries) == 5
            and all(
                item["deterministic_validation_outcome"] == "succeeded"
                for item in question_summaries
            )
            and (args.keep_documents or cleaned_documents == len(newly_created))
        ),
    }
    write_json(args.summary_output, summary)
    return summary


def main() -> int:
    args = parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
