from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from scholartrace.contracts import (
    Claim,
    DocuMindBinding,
    Evidence,
    Paper,
    PaperSource,
)
from scholartrace.evidence.models import RetrievalChunk
from scholartrace.verification.validator import EvidenceValidator
from scholartrace.verification_ablation.models import (
    AblationFrozenInput,
    AblationSourceRef,
    canonical_sha256,
)

NOW = datetime(2026, 9, 21, tzinfo=UTC)
PAPER_ID = "doi:10.1000/synthetic-sa04"
EVIDENCE_ID = "evidence:synthetic:01"
CHUNK_ID = "a" * 64
QUOTE = "The synthetic method improves exact match by 10 percent."


def make_synthetic_inputs() -> dict[str, AblationFrozenInput]:
    paper = Paper(
        canonical_paper_id=PAPER_ID,
        title="Synthetic SA-04 Paper",
        normalized_title="synthetic sa04 paper",
        authors=["Synthetic Author"],
        publication_year=2024,
        doi="10.1000/synthetic-sa04",
        openalex_id="W-SYNTHETIC-SA04",
        access_level="fulltext",
        sources=[
            PaperSource(
                source="openalex",
                source_id="https://openalex.org/W-SYNTHETIC-SA04",
                retrieved_at=NOW,
                record_sha256="b" * 64,
            )
        ],
    )
    binding = DocuMindBinding(
        canonical_paper_id=PAPER_ID,
        document_key="c" * 64,
        index_id="d" * 64,
        source_sha256="e" * 64,
        documind_version="2.2.0",
        retrieval_schema_version="1.0",
    )
    quote_hash = hashlib.sha256(QUOTE.encode("utf-8")).hexdigest()
    evidence = Evidence(
        evidence_id=EVIDENCE_ID,
        canonical_paper_id=PAPER_ID,
        quote=QUOTE,
        evidence_level="fulltext",
        content_sha256=quote_hash,
        chunk_content_sha256=quote_hash,
        document_key=binding.document_key,
        index_id=binding.index_id,
        source_sha256=binding.source_sha256,
        section="Results",
        page_number=1,
        chunk_id=CHUNK_ID,
        char_start=0,
        char_end=len(QUOTE),
        retrieval_run_id="retrieval:synthetic:sa04",
    )
    chunk = RetrievalChunk(
        chunk_id=CHUNK_ID,
        content=QUOTE,
        content_sha256=quote_hash,
        source="synthetic.pdf",
        page_number=1,
        distance=0,
        rank=1,
    )
    claims = [
        Claim(
            claim_id="claim:synthetic:supported",
            text=QUOTE,
            claim_type="result",
            evidence_ids=[EVIDENCE_ID],
            origin="author_stated",
            importance="supporting",
        ),
        Claim(
            claim_id="claim:synthetic:comparison",
            text="The synthetic method improves exact match.",
            claim_type="comparison",
            evidence_ids=[EVIDENCE_ID],
            origin="cross_paper_synthesis",
            importance="critical",
        ),
        Claim(
            claim_id="claim:synthetic:bounded",
            text="The synthetic method improves the measured test result.",
            claim_type="inference",
            evidence_ids=[EVIDENCE_ID],
            origin="system_inferred",
            importance="supporting",
        ),
    ]
    validation = EvidenceValidator().validate(
        claims=claims,
        evidence=[evidence],
        papers=[paper],
        bindings=[binding],
        chunks_by_paper={PAPER_ID: [chunk]},
        validated_at=NOW,
    )
    assert validation.outcome == "succeeded"
    payload = {
        "schema_version": "1.0",
        "purpose": "scholartrace-sa02-pre-semantic-verification-input",
        "question_id": "synthetic-sa04-pilot",
        "split": "pilot",
        "question": "What does the synthetic Evidence support?",
        "categories": ["ordinary_fact", "evidence_incomplete"],
        "source": AblationSourceRef(
            artifact_id="synthetic:sa04",
            report_path="tests/sa04_synthetic.py",
            report_id="report:synthetic:sa04",
            report_question="Synthetic question",
            report_sha256="f" * 64,
            report_content_sha256="0" * 64,
        ).model_dump(mode="json"),
        "papers": [paper.model_dump(mode="json")],
        "bindings": [binding.model_dump(mode="json")],
        "chunks_by_paper": {PAPER_ID: [chunk.model_dump(mode="json")]},
        "claims": [claim.model_dump(mode="json") for claim in claims],
        "evidence": [evidence.model_dump(mode="json")],
        "deterministic_validation": validation.model_dump(mode="json"),
        "semantic_verification_results_included": False,
    }
    payload["input_sha256"] = canonical_sha256(payload)
    item = AblationFrozenInput.model_validate(payload)
    return {item.question_id: item}
