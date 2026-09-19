from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scholartrace.contracts import DocuMindBinding, ModelUsageRecord, Paper
from scholartrace.evidence.analysis import OllamaPaperAnalyzer
from scholartrace.evidence.artifacts import BudgetExceededError, persist_m2_artifacts
from scholartrace.evidence.client import RetrievalResult
from scholartrace.evidence.models import (
    DocuMindRetrieveResponse,
    PaperAnalysisDraft,
    RetrievalAudit,
)
from scholartrace.evidence.pipeline import EvidencePipelineResult
from scholartrace.evidence.report import build_evidence_report

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "documind" / "m2_three_papers.json"
NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)


def _result(*, oversized_tokens: bool = False) -> EvidencePipelineResult:
    fixture = json.loads(FIXTURE_PATH.read_text("utf-8"))
    bundles = []
    audits = []
    usage = []
    for index, case in enumerate(fixture["papers"]):
        paper = Paper.model_validate(case["paper"])
        binding = DocuMindBinding.model_validate(case["binding"])
        response = DocuMindRetrieveResponse.model_validate(case["retrieve_response"])
        audit = RetrievalAudit(
            retrieval_run_id=f"retrieval:m2:artifact-{index}",
            canonical_paper_id=paper.canonical_paper_id,
            document_key=binding.document_key,
            index_id=binding.index_id,
            source_sha256=binding.source_sha256,
            query_sha256="9" * 64,
            started_at=NOW,
            completed_at=NOW,
            duration_seconds=1,
            attempts=1,
            status="succeeded",
            service_version="2.2.0",
            retrieval_version="dense-v1",
            chunk_count=1,
        )
        bundle = OllamaPaperAnalyzer._build_bundle(
            paper=paper,
            binding=binding,
            retrieval=RetrievalResult(response=response, audit=audit),
            question=fixture["question"],
            draft=PaperAnalysisDraft.model_validate(case["analysis_draft"]),
            generated_at=NOW,
            input_payload={"fixture": index},
        )
        bundles.append(bundle)
        audits.append(audit)
        usage.append(
            ModelUsageRecord(
                node="paper_analysis",
                profile_id="local-qwen3-8b",
                provider="ollama",
                model_name="qwen3:8b",
                model_version="500a1f067a9f",
                input_tokens=160001 if oversized_tokens and index == 0 else 300,
                output_tokens=90,
                call_count=1,
                duration_seconds=2,
            )
        )
    report = build_evidence_report(
        question=fixture["question"],
        analyses=bundles,
        generated_at=NOW,
    )
    return EvidencePipelineResult(
        report=report,
        retrieval_audits=audits,
        model_usage=usage,
        started_at=NOW,
        completed_at=NOW + timedelta(seconds=8),
    )


def test_persisted_m2_artifacts_have_budget_and_byte_accurate_hashes(tmp_path: Path) -> None:
    output = tmp_path / "m2-artifacts"
    persisted = persist_m2_artifacts(
        root=ROOT,
        output_dir=output,
        result=_result(),
        git_commit="a" * 40,
        worktree_dirty=True,
        source_tree_sha256="b" * 64,
    )

    manifest = persisted.manifest
    assert manifest.budget.usage.fulltext_papers == 3
    assert manifest.budget.usage.rag_calls == 3
    assert manifest.budget.usage.model_calls == 3
    assert manifest.budget.usage.llm_input_tokens == 900
    assert manifest.budget.usage.llm_output_tokens == 270
    assert manifest.budget.usage.external_cost_cny == 0
    assert manifest.outcome == "succeeded"
    assert len(manifest.data_snapshot_refs) == 4
    assert (
        persisted.report_markdown_sha256
        == hashlib.sha256((output / "evidence_report.md").read_bytes()).hexdigest()
    )
    assert (
        persisted.manifest_sha256
        == hashlib.sha256((output / "run_manifest.json").read_bytes()).hexdigest()
    )
    assert manifest.data_snapshot_refs[1].content_sha256 == persisted.report_markdown_sha256


def test_budget_violation_fails_before_any_artifact_is_written(tmp_path: Path) -> None:
    output = tmp_path / "m2-artifacts"
    with pytest.raises(BudgetExceededError, match="llm_input_tokens"):
        persist_m2_artifacts(
            root=ROOT,
            output_dir=output,
            result=_result(oversized_tokens=True),
            git_commit="a" * 40,
            worktree_dirty=True,
            source_tree_sha256="b" * 64,
        )
    assert not output.exists()


def test_live_artifacts_require_explicit_provider_identity_before_writing(tmp_path: Path) -> None:
    output = tmp_path / "live"
    with pytest.raises(ValueError, match="provider identity"):
        persist_m2_artifacts(
            root=ROOT,
            output_dir=output,
            result=_result(),
            git_commit="a" * 40,
            worktree_dirty=True,
            source_tree_sha256="b" * 64,
            capability_id="single-document-dense-retrieval-live-smoke",
        )
    assert not output.exists()


def test_live_artifacts_record_explicit_provider_identity(tmp_path: Path) -> None:
    persisted = persist_m2_artifacts(
        root=ROOT,
        output_dir=tmp_path / "live",
        result=_result(),
        git_commit="a" * 40,
        worktree_dirty=True,
        source_tree_sha256="b" * 64,
        capability_id="single-document-dense-retrieval-live-smoke",
        documind_version="2.2.0",
        documind_commit="c" * 40,
    )
    provider = next(b for b in persisted.manifest.service_baselines if b.service == "documind")
    assert provider.version == "2.2.0"
    assert provider.git_commit == "c" * 40
