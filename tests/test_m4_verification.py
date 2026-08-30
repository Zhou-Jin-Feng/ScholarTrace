from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scholartrace.contracts import (
    Budget,
    BudgetLimits,
    BudgetUsage,
    Claim,
    DocuMindBinding,
    Evidence,
    ModelRoutingPolicy,
    Paper,
    PaperSource,
    Verification,
)
from scholartrace.evidence.models import RetrievalChunk
from scholartrace.verification.followup import FollowUpPolicy
from scholartrace.verification.gate import VerificationReportGate
from scholartrace.verification.models import (
    CitationRequirement,
    SemanticVerificationDraft,
    ValidationBundle,
)
from scholartrace.verification.pipeline import M4ReliabilityPipeline
from scholartrace.verification.validator import EvidenceValidator
from scholartrace.verification.verifier import (
    VerifierKind,
    VerifierRunner,
    VerifierUnavailableError,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 30, 5, tzinfo=UTC)
PAPER_ID = "doi:10.1000/verified"
QUOTE = "The method improves exact match by 10% on the test set."
CHUNK_ID = "d" * 64


def _paper() -> Paper:
    return Paper(
        canonical_paper_id=PAPER_ID,
        title="Verified test paper",
        normalized_title="verified test paper",
        authors=["Test Author"],
        publication_year=2024,
        doi="10.1000/verified",
        openalex_id="W100",
        access_level="fulltext",
        sources=[
            PaperSource(
                source="openalex",
                source_id="https://openalex.org/W100",
                retrieved_at=NOW,
                record_sha256="a" * 64,
            )
        ],
    )


def _binding(*, index_id: str = "b" * 64) -> DocuMindBinding:
    return DocuMindBinding(
        canonical_paper_id=PAPER_ID,
        document_key="a" * 64,
        index_id=index_id,
        source_sha256="c" * 64,
        documind_version="2.2.0",
        retrieval_schema_version="1.0",
    )


def _chunk() -> RetrievalChunk:
    return RetrievalChunk(
        chunk_id=CHUNK_ID,
        content=QUOTE,
        content_sha256=hashlib.sha256(QUOTE.encode()).hexdigest(),
        source="paper.pdf",
        page_number=3,
        distance=0.1,
        rank=1,
    )


def _evidence(
    evidence_id: str = "evidence:m4:support",
    *,
    index_id: str = "b" * 64,
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        canonical_paper_id=PAPER_ID,
        quote=QUOTE,
        evidence_level="fulltext",
        content_sha256=hashlib.sha256(QUOTE.encode()).hexdigest(),
        chunk_content_sha256=hashlib.sha256(QUOTE.encode()).hexdigest(),
        document_key="a" * 64,
        index_id=index_id,
        source_sha256="c" * 64,
        page_number=3,
        chunk_id=CHUNK_ID,
        char_start=0,
        char_end=len(QUOTE),
        retrieval_run_id="retrieval:m4:test",
    )


def _claim(
    claim_id: str = "claim:m4:supported",
    *,
    text: str = "The method improves exact match by 10% on the test set.",
    evidence_ids: list[str] | None = None,
    counter_evidence_ids: list[str] | None = None,
    importance: str = "critical",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        text=text,
        claim_type="result",
        evidence_ids=evidence_ids if evidence_ids is not None else ["evidence:m4:support"],
        counter_evidence_ids=counter_evidence_ids or [],
        origin="author_stated",
        importance=importance,  # type: ignore[arg-type]
    )


def _validate(
    claims: list[Claim],
    evidence: list[Evidence],
    *,
    binding: DocuMindBinding | None = None,
    requirements: list[CitationRequirement] | None = None,
) -> ValidationBundle:
    return EvidenceValidator().validate(
        claims=claims,
        evidence=evidence,
        papers=[_paper()],
        bindings=[binding or _binding()],
        chunks_by_paper={PAPER_ID: [_chunk()]},
        citation_requirements=requirements,
        validated_at=NOW,
    )


def test_validator_accepts_exact_binding_chunk_page_quote_and_number() -> None:
    bundle = _validate([_claim()], [_evidence()])
    assert bundle.outcome == "succeeded"
    assert bundle.results[0].passed


def test_validator_blocks_binding_number_and_required_citation_mismatches() -> None:
    claim = _claim(text="The method improves exact match by 11% on the test set.")
    requirement = CitationRequirement(
        claim_id=claim.claim_id,
        citing_paper_id=PAPER_ID,
        cited_paper_id="openalex:W200",
    )
    bundle = _validate(
        [claim],
        [_evidence(index_id="e" * 64)],
        binding=_binding(),
        requirements=[requirement],
    )
    codes = {issue.code for issue in bundle.results[0].issues}
    assert bundle.outcome == "failed"
    assert codes == {"citation_edge_missing", "index_id_mismatch", "numeric_mismatch"}


class _FixtureVerifier:
    verifier_kind: VerifierKind = "fixture"
    profile_id: str | None = None

    def __init__(self, drafts: dict[str, SemanticVerificationDraft]) -> None:
        self.drafts = drafts
        self.calls: list[str] = []

    async def verify(
        self,
        *,
        claim: Claim,
        evidence: list[Evidence],
    ) -> SemanticVerificationDraft:
        assert evidence
        self.calls.append(claim.claim_id)
        return self.drafts[claim.claim_id]


class _ModelVerifier(_FixtureVerifier):
    verifier_kind: VerifierKind = "model"
    profile_id: str | None = "api-strong"


def test_disabled_api_strong_verifier_fails_closed() -> None:
    claim = _claim()
    validation = _validate([claim], [_evidence()])
    policy = ModelRoutingPolicy.model_validate(
        json.loads((ROOT / "contracts/examples/m0_bundle.json").read_text("utf-8"))[
            "ModelRoutingPolicy"
        ]
    )
    backend = _ModelVerifier(
        {
            claim.claim_id: SemanticVerificationDraft(
                status="supported",
                reason="Fixture should never run.",
                recommended_action="keep",
            )
        }
    )
    runner = VerifierRunner(backend=backend, policy=policy)
    with pytest.raises(VerifierUnavailableError, match="disabled"):
        asyncio.run(
            runner.verify(
                claims=[claim],
                evidence=[_evidence()],
                validations=validation.results,
                verified_at=NOW,
            )
        )
    assert backend.calls == []


def test_conflict_is_marked_and_unsupported_critical_claim_is_excluded() -> None:
    support = _evidence()
    counter = _evidence("evidence:m4:counter")
    conflicted = _claim(
        "claim:m4:conflicted",
        evidence_ids=[support.evidence_id],
        counter_evidence_ids=[counter.evidence_id],
    )
    unsupported = _claim(
        "claim:m4:unsupported",
        evidence_ids=["evidence:m4:missing"],
    )
    validation = _validate([conflicted, unsupported], [support, counter])
    backend = _FixtureVerifier(
        {
            conflicted.claim_id: SemanticVerificationDraft(
                status="conflicted",
                reason="Support and counter-evidence disagree under the fixture conditions.",
                recommended_action="follow_up",
            )
        }
    )
    verifications = asyncio.run(
        VerifierRunner(
            backend=backend,
            policy=None,
            allow_fixture=True,
        ).verify(
            claims=[conflicted, unsupported],
            evidence=[support, counter],
            validations=validation.results,
            verified_at=NOW,
        )
    )
    assert backend.calls == [conflicted.claim_id]
    by_claim = {item.claim_id: item for item in verifications}
    assert by_claim[unsupported.claim_id].verifier == "deterministic"
    assert by_claim[unsupported.claim_id].status == "unsupported"

    gate = VerificationReportGate().apply(
        claims=[conflicted, unsupported],
        verifications=verifications,
    )
    disposition = {item.claim_id: item for item in gate.dispositions}
    assert disposition[conflicted.claim_id].marker == "[CONFLICTED]"
    assert disposition[conflicted.claim_id].included
    assert not disposition[unsupported.claim_id].included
    assert gate.blocked_critical_claim_ids == [unsupported.claim_id]
    assert gate.report_safe


def test_partially_supported_claim_is_included_with_visible_marker() -> None:
    claim = _claim("claim:m4:partial")
    validation = _validate([claim], [_evidence()])
    backend = _FixtureVerifier(
        {
            claim.claim_id: SemanticVerificationDraft(
                status="partially_supported",
                reason="The evidence supports the direction but not every stated condition.",
                recommended_action="weaken",
            )
        }
    )
    verifications = asyncio.run(
        VerifierRunner(backend=backend, policy=None, allow_fixture=True).verify(
            claims=[claim],
            evidence=[_evidence()],
            validations=validation.results,
            verified_at=NOW,
        )
    )
    gate = VerificationReportGate().apply(claims=[claim], verifications=verifications)
    assert gate.dispositions[0].included
    assert gate.dispositions[0].marker == "[PARTIALLY SUPPORTED]"


def test_reliability_pipeline_creates_only_one_budgeted_follow_up() -> None:
    supported = _claim("claim:m4:valid")
    unsupported = _claim(
        "claim:m4:needs-follow-up",
        evidence_ids=["evidence:m4:missing"],
    )
    backend = _FixtureVerifier(
        {
            supported.claim_id: SemanticVerificationDraft(
                status="supported",
                reason="The supplied quote directly supports the claim.",
                recommended_action="keep",
            )
        }
    )
    pipeline = M4ReliabilityPipeline(
        verifier=VerifierRunner(backend=backend, policy=None, allow_fixture=True)
    )
    budget = Budget(
        limits=BudgetLimits(
            max_rounds=2,
            max_queries=2,
            max_api_calls=2,
        ),
        usage=BudgetUsage(queries=1),
    )

    first = asyncio.run(
        pipeline.run(
            task_id="task:m4:fixture",
            claims=[supported, unsupported],
            evidence=[_evidence()],
            papers=[_paper()],
            bindings=[_binding()],
            chunks_by_paper={PAPER_ID: [_chunk()]},
            claim_subquestions={
                supported.claim_id: "subq:m4:supported",
                unsupported.claim_id: "subq:m4:missing",
            },
            budget=budget,
            completed_at=NOW,
        )
    )
    assert first.outcome == "degraded"
    assert len(first.follow_up_requests) == 1
    request = first.follow_up_requests[0]
    assert request.claim_id == unsupported.claim_id
    assert request.round_index == 1
    assert request.max_additional_queries == 1

    second = asyncio.run(
        pipeline.run(
            task_id="task:m4:fixture",
            claims=[supported, unsupported],
            evidence=[_evidence()],
            papers=[_paper()],
            bindings=[_binding()],
            chunks_by_paper={PAPER_ID: [_chunk()]},
            claim_subquestions={
                supported.claim_id: "subq:m4:supported",
                unsupported.claim_id: "subq:m4:missing",
            },
            budget=budget,
            completed_at=NOW,
            existing_follow_ups=[request],
        )
    )
    assert second.follow_up_requests == []

    exhausted = budget.model_copy(
        update={"usage": budget.usage.model_copy(update={"queries": 2})}
    )
    no_budget = asyncio.run(
        pipeline.run(
            task_id="task:m4:fixture",
            claims=[supported, unsupported],
            evidence=[_evidence()],
            papers=[_paper()],
            bindings=[_binding()],
            chunks_by_paper={PAPER_ID: [_chunk()]},
            claim_subquestions={unsupported.claim_id: "subq:m4:missing"},
            budget=exhausted,
            completed_at=NOW,
        )
    )
    assert no_budget.follow_up_requests == []


def test_missing_citation_follow_up_targets_a_citation_path() -> None:
    claim = _claim("claim:m4:citation-follow-up")
    request = FollowUpPolicy().select(
        task_id="task:m4:fixture",
        claims=[claim],
        verifications=[
            Verification(
                verification_id="verification:m4:citation-follow-up",
                claim_id=claim.claim_id,
                status="unsupported",
                checked_evidence_ids=["evidence:m4:support"],
                reason="Deterministic validation failed: citation_edge_missing",
                recommended_action="follow_up",
                verifier="deterministic",
                verified_at=NOW,
            )
        ],
        claim_subquestions={claim.claim_id: "subq:m4:citation"},
        budget=Budget(
            limits=BudgetLimits(max_rounds=2, max_queries=2, max_api_calls=2),
            usage=BudgetUsage(queries=1),
        ),
    )[0]
    assert request.missing_evidence_type == "citation_path"
