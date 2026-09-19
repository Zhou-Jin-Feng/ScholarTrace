from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scholartrace.contracts import DocuMindBinding, Paper
from scholartrace.evidence.analysis import OllamaPaperAnalyzer
from scholartrace.evidence.client import RetrievalResult
from scholartrace.evidence.models import (
    DocuMindRetrieveResponse,
    PaperAnalysisDraft,
    RetrievalAudit,
)
from scholartrace.evidence.report import build_evidence_report
from scholartrace.scholargraph.pilot_input import load_m2_pilot_artifacts

ROOT = Path(__file__).resolve().parents[1]
PAPER_FIXTURE = ROOT / "tests" / "fixtures" / "documind" / "m2_three_papers.json"
NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)


@pytest.fixture
def synthetic_report(tmp_path: Path) -> Path:
    """Build a fixture-only packet without private Evidence, HTTP or model calls."""
    fixture = json.loads(PAPER_FIXTURE.read_text("utf-8"))
    bundles = []
    for index, case in enumerate(fixture["papers"]):
        paper = Paper.model_validate(case["paper"])
        binding = DocuMindBinding.model_validate(case["binding"])
        response = DocuMindRetrieveResponse.model_validate(case["retrieve_response"])
        audit = RetrievalAudit(
            retrieval_run_id=f"retrieval:fixture:pilot-{index}",
            canonical_paper_id=paper.canonical_paper_id,
            document_key=binding.document_key,
            index_id=binding.index_id,
            source_sha256=binding.source_sha256,
            query_sha256="9" * 64,
            started_at=NOW,
            completed_at=NOW,
            duration_seconds=0,
            attempts=1,
            status="succeeded",
            service_version="2.2.0",
            retrieval_version="dense-v1",
            chunk_count=len(response.chunks),
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
        bundle.paper_card.generator = "fixture"
        bundle.paper_card.model_profile_id = None
        bundles.append(bundle)
    report = build_evidence_report(question=fixture["question"], analyses=bundles, generated_at=NOW)
    output = tmp_path / "synthetic-evidence-report.json"
    output.write_text(report.model_dump_json(), encoding="utf-8")
    return output


def test_fixture_packet_passes_real_loader_and_validator(synthetic_report: Path) -> None:
    packet = load_m2_pilot_artifacts(report_path=synthetic_report, paper_fixture_path=PAPER_FIXTURE)
    assert packet.validation.outcome == "succeeded"
    assert all(row.passed for row in packet.validation.results)
    assert len(packet.claims) == len(packet.evidence) == 3
    assert len(packet.papers) == len(packet.bindings) == 3
    assert len(packet.paper_pool_sha256) == 64
    assert packet.source_report_sha256 == hashlib.sha256(synthetic_report.read_bytes()).hexdigest()
    again = load_m2_pilot_artifacts(report_path=synthetic_report, paper_fixture_path=PAPER_FIXTURE)
    assert again.paper_pool_sha256 == packet.paper_pool_sha256
    assert again.source_report_sha256 == packet.source_report_sha256


@pytest.mark.parametrize("kind", ["report_hash", "source_binding", "cross_paper"])
def test_loader_rejects_tampered_fixture(synthetic_report: Path, kind: str) -> None:
    payload = json.loads(synthetic_report.read_text("utf-8"))
    if kind == "report_hash":
        payload["content_markdown"] += "tampered"
    elif kind == "source_binding":
        audit = payload["analyses"][0]["retrieval_audit"]
        original = audit["source_sha256"]
        audit["source_sha256"] = hashlib.sha256(("tampered:" + original).encode()).hexdigest()
        assert audit["source_sha256"] != original
    else:
        payload["analyses"][0]["evidence"][0]["canonical_paper_id"] = "arxiv:0000.00000"
    synthetic_report.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_m2_pilot_artifacts(report_path=synthetic_report, paper_fixture_path=PAPER_FIXTURE)


def test_loader_rejects_paper_pool_drift(synthetic_report: Path, tmp_path: Path) -> None:
    fixture = json.loads(PAPER_FIXTURE.read_text("utf-8"))
    fixture["papers"].pop()
    path = tmp_path / "different-pool.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(ValueError, match="paper pool drifted"):
        load_m2_pilot_artifacts(report_path=synthetic_report, paper_fixture_path=path)


def test_loader_missing_report_fails_instead_of_skipping(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_m2_pilot_artifacts(
            report_path=tmp_path / "missing.json", paper_fixture_path=PAPER_FIXTURE
        )


@pytest.mark.parametrize("mode", ["valid", "missing", "wrong_counts", "invalid"])
def test_explicit_packet_cli_reports_failure_without_raw_evidence(
    synthetic_report: Path, mode: str
) -> None:
    if mode == "invalid":
        synthetic_report.write_text("invalid-private-payload", encoding="utf-8")
    path = synthetic_report.with_name("missing.json") if mode == "missing" else synthetic_report
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/verify_m2_pilot_packet.py"),
            "--report",
            str(path),
            "--expected-claims",
            "99" if mode == "wrong_counts" else "3",
            "--expected-evidence",
            "3",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == (0 if mode == "valid" else 1)
    assert json.loads(result.stdout)["result"] == ("PASS" if mode == "valid" else "FAIL")
    assert "invalid-private-payload" not in result.stdout + result.stderr
    assert str(path) not in result.stdout + result.stderr
