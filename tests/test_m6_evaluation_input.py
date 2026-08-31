from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from scholartrace.contracts import Claim, Evidence, Verification
from scholartrace.scholargraph.evaluation_input import (
    EvaluationInputError,
    prepare_evidence_input,
)
from scholartrace.verification.gate import VerificationReportGate


def _evidence(evidence_id: str, quote: str) -> Evidence:
    digest = hashlib.sha256(quote.encode("utf-8")).hexdigest()
    return Evidence(
        evidence_id=evidence_id,
        canonical_paper_id="doi:10.0000/test-paper",
        quote=quote,
        evidence_level="fulltext",
        content_sha256=digest,
        chunk_content_sha256="b" * 64,
        document_key="c" * 64,
        index_id="d" * 64,
        section="Methods",
        page_number=3,
        chunk_id="e" * 64,
        source_sha256="f" * 64,
        retrieval_run_id="run:documind:test",
    )


def _verification(
    claim_id: str,
    status: str,
    evidence_ids: list[str],
) -> Verification:
    return Verification.model_validate(
        {
            "verification_id": f"verification:{claim_id}",
            "claim_id": claim_id,
            "status": status,
            "checked_evidence_ids": evidence_ids,
            "reason": "The checked Evidence supports the bounded wording.",
            "recommended_action": "keep" if status == "supported" else "remove",
            "verifier": "model",
            "verified_at": datetime(2026, 8, 30, tzinfo=UTC),
        }
    )


def _artifacts() -> tuple[list[Claim], list[Evidence], list[Verification]]:
    evidence_id = "evidence:documind:01"
    supported = Claim(
        claim_id="claim:supported",
        text="A retrieval evaluator selects corrective actions.",
        claim_type="fact",
        evidence_ids=[evidence_id],
        origin="author_stated",
        importance="critical",
    )
    unsupported = Claim(
        claim_id="claim:unsupported",
        text="The method always improves every benchmark.",
        claim_type="result",
        evidence_ids=[],
        origin="system_inferred",
        importance="supporting",
    )
    return (
        [supported, unsupported],
        [_evidence(evidence_id, "A retrieval evaluator selects corrective actions.")],
        [
            _verification(supported.claim_id, "supported", [evidence_id]),
            _verification(unsupported.claim_id, "unsupported", []),
        ],
    )


def test_prepare_input_includes_only_report_safe_claims_and_evidence() -> None:
    claims, evidence, verifications = _artifacts()
    gate = VerificationReportGate().apply(claims=claims, verifications=verifications)
    prepared = prepare_evidence_input(
        question_id="sg-eligible-02",
        paper_pool_sha256="a" * 64,
        claims=claims,
        evidence=evidence,
        verifications=verifications,
        report_gate=gate,
    )
    payload = json.loads(prepared.evidence_context)
    assert prepared.included_claim_count == 1
    assert prepared.evidence_count == 1
    assert prepared.allowed_evidence_ids == frozenset({"evidence:documind:01"})
    assert payload["claims"][0]["claim_id"] == "claim:supported"
    assert "always improves" not in prepared.evidence_context
    assert payload["evidence"][0]["evidence_level"] == "fulltext"
    assert len(prepared.input_sha256) == 64


def test_missing_or_unchecked_evidence_fails_closed() -> None:
    claims, evidence, verifications = _artifacts()
    gate = VerificationReportGate().apply(claims=claims, verifications=verifications)
    with pytest.raises(EvaluationInputError, match="missing"):
        prepare_evidence_input(
            question_id="sg-eligible-02",
            paper_pool_sha256="a" * 64,
            claims=claims,
            evidence=[],
            verifications=verifications,
            report_gate=gate,
        )
    drifted = [
        verifications[0].model_copy(update={"checked_evidence_ids": []}),
        verifications[1],
    ]
    with pytest.raises(EvaluationInputError, match="unchecked"):
        prepare_evidence_input(
            question_id="sg-eligible-02",
            paper_pool_sha256="a" * 64,
            claims=claims,
            evidence=evidence,
            verifications=drifted,
            report_gate=gate,
        )


def test_unsafe_gate_and_oversized_context_are_rejected() -> None:
    claims, evidence, verifications = _artifacts()
    gate = VerificationReportGate().apply(claims=claims, verifications=verifications)
    with pytest.raises(EvaluationInputError, match="not safe"):
        prepare_evidence_input(
            question_id="sg-eligible-02",
            paper_pool_sha256="a" * 64,
            claims=claims,
            evidence=evidence,
            verifications=verifications,
            report_gate=gate.model_copy(update={"report_safe": False}),
        )
    long_quote = "x" * 5000
    long_evidence = _evidence("evidence:documind:01", long_quote)
    with pytest.raises(EvaluationInputError, match="exceeds"):
        prepare_evidence_input(
            question_id="sg-eligible-02",
            paper_pool_sha256="a" * 64,
            claims=claims,
            evidence=[long_evidence],
            verifications=verifications,
            report_gate=gate,
            max_context_characters=1000,
        )
    prepared = prepare_evidence_input(
        question_id="sg-eligible-02",
        paper_pool_sha256="a" * 64,
        claims=claims,
        evidence=[long_evidence],
        verifications=verifications,
        report_gate=gate,
        max_context_characters=10_000,
    )
    assert len(prepared.evidence_context) > 5000
