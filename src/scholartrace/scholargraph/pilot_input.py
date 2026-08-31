"""Load and deterministically validate the real M2 Evidence packet for an M6 pilot."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from scholartrace.contracts import Claim, DocuMindBinding, Evidence, Paper
from scholartrace.evidence.models import EvidenceReportArtifact, RetrievalChunk
from scholartrace.verification.models import ValidationBundle
from scholartrace.verification.validator import EvidenceValidator


@dataclass(frozen=True, slots=True)
class M2PilotArtifacts:
    claims: list[Claim]
    evidence: list[Evidence]
    papers: list[Paper]
    bindings: list[DocuMindBinding]
    chunks_by_paper: Mapping[str, list[RetrievalChunk]]
    validation: ValidationBundle
    paper_pool_sha256: str
    source_report_sha256: str


def load_m2_pilot_artifacts(
    *,
    report_path: Path,
    paper_fixture_path: Path,
) -> M2PilotArtifacts:
    report_bytes = report_path.read_bytes()
    report = EvidenceReportArtifact.model_validate_json(report_bytes)
    fixture = json.loads(paper_fixture_path.read_text("utf-8"))
    fixture_papers = {
        paper.canonical_paper_id: paper
        for paper in (
            Paper.model_validate(row["paper"]) for row in fixture["papers"]
        )
    }
    paper_ids = [row.paper_card.canonical_paper_id for row in report.analyses]
    if set(paper_ids) != set(fixture_papers):
        raise ValueError("M2 live report paper pool drifted from its frozen fixture")
    papers = [fixture_papers[paper_id] for paper_id in sorted(paper_ids)]
    claims = [claim for row in report.analyses for claim in row.claims]
    evidence = [item for row in report.analyses for item in row.evidence]
    bindings = [
        DocuMindBinding(
            canonical_paper_id=row.retrieval_audit.canonical_paper_id,
            document_key=row.retrieval_audit.document_key,
            index_id=row.retrieval_audit.index_id,
            source_sha256=row.retrieval_audit.source_sha256,
            documind_version=row.retrieval_audit.service_version or "",
            retrieval_schema_version="1.0",
        )
        for row in report.analyses
    ]
    chunks_by_paper: dict[str, list[RetrievalChunk]] = {}
    for paper_id in sorted(paper_ids):
        unique: dict[str, Evidence] = {}
        for item in evidence:
            if item.canonical_paper_id == paper_id:
                if item.chunk_id is None or item.chunk_content_sha256 is None:
                    raise ValueError("M2 fulltext Evidence has no chunk provenance")
                previous = unique.get(item.chunk_id)
                if previous is not None and previous.quote != item.quote:
                    raise ValueError("one M2 chunk ID resolved to different exact quotes")
                unique[item.chunk_id] = item
        chunks_by_paper[paper_id] = [
            RetrievalChunk(
                chunk_id=item.chunk_id or "",
                content=item.quote,
                content_sha256=item.chunk_content_sha256 or "",
                source=item.section or item.source_url or "documind",
                page_number=item.page_number,
                distance=0,
                rank=index,
            )
            for index, item in enumerate(
                sorted(unique.values(), key=lambda value: value.chunk_id or ""),
                1,
            )
        ]
    validation = EvidenceValidator().validate(
        claims=claims,
        evidence=evidence,
        papers=papers,
        bindings=bindings,
        chunks_by_paper=chunks_by_paper,
        validated_at=report.generated_at,
    )
    if validation.outcome != "succeeded" or any(
        not item.passed for item in validation.results
    ):
        issue_codes = sorted(
            {
                issue.code
                for result in validation.results
                if not result.passed
                for issue in result.issues
            }
        )
        suffix = ", ".join(issue_codes) if issue_codes else "unknown_issue"
        raise ValueError(
            f"M2 live Evidence failed deterministic M4 validation: {suffix}"
        )
    pool_payload = json.dumps(
        {
            "paper_ids": sorted(paper_ids),
            "source_sha256": sorted(binding.source_sha256 for binding in bindings),
            "report_content_sha256": report.content_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return M2PilotArtifacts(
        claims=claims,
        evidence=evidence,
        papers=papers,
        bindings=bindings,
        chunks_by_paper=chunks_by_paper,
        validation=validation,
        paper_pool_sha256=hashlib.sha256(pool_payload).hexdigest(),
        source_report_sha256=hashlib.sha256(report_bytes).hexdigest(),
    )
