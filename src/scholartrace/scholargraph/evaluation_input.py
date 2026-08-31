"""Prepare private, report-safe DocuMind Evidence inputs for B3/B4 generation."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from scholartrace.contracts import Claim, Evidence, Verification
from scholartrace.scholargraph.experiment import evidence_input_sha256
from scholartrace.verification.models import ReportGateResult


class EvaluationInputError(ValueError):
    """Validated research artifacts cannot form a safe report input."""


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class PreparedEvidenceInput:
    evidence_context: str
    allowed_evidence_ids: frozenset[str]
    input_sha256: str
    included_claim_count: int
    evidence_count: int


def prepare_evidence_input(
    *,
    question_id: str,
    paper_pool_sha256: str,
    claims: list[Claim],
    evidence: list[Evidence],
    verifications: list[Verification],
    report_gate: ReportGateResult,
    max_context_characters: int = 20_000,
) -> PreparedEvidenceInput:
    """Build canonical private JSON from artifacts that already passed M4 gates."""

    if max_context_characters < 1000:
        raise ValueError("evaluation Evidence context limit must be at least 1000")
    if not report_gate.report_safe:
        raise EvaluationInputError("M4 report gate is not safe for synthesis")
    claim_by_id = _unique_by_id(claims, lambda item: item.claim_id, "Claim")
    evidence_by_id = _unique_by_id(
        evidence,
        lambda item: item.evidence_id,
        "Evidence",
    )
    verification_by_claim = _unique_by_id(
        verifications,
        lambda item: item.claim_id,
        "Verification",
    )
    disposition_by_claim = _unique_by_id(
        report_gate.dispositions,
        lambda item: item.claim_id,
        "report disposition",
    )
    if set(disposition_by_claim) != set(claim_by_id):
        raise EvaluationInputError("report gate does not cover the exact Claim set")

    context_claims: list[dict[str, object]] = []
    allowed_ids: set[str] = set()
    for claim_id in sorted(claim_by_id):
        claim = claim_by_id[claim_id]
        disposition = disposition_by_claim[claim_id]
        if not disposition.included:
            if disposition.verification_status != "unsupported":
                raise EvaluationInputError(
                    f"{claim_id}: excluded Claim has a non-unsupported status"
                )
            continue
        verification = verification_by_claim.get(claim_id)
        if verification is None or verification.status != disposition.verification_status:
            raise EvaluationInputError(f"{claim_id}: verification and report gate drifted")
        claim_evidence_ids = set(claim.evidence_ids) | set(claim.counter_evidence_ids)
        checked_ids = set(verification.checked_evidence_ids)
        if not claim_evidence_ids or not claim_evidence_ids.issubset(checked_ids):
            raise EvaluationInputError(
                f"{claim_id}: included Claim has unchecked Evidence references"
            )
        missing = claim_evidence_ids - set(evidence_by_id)
        if missing:
            raise EvaluationInputError(f"{claim_id}: referenced Evidence is missing")
        allowed_ids.update(claim_evidence_ids)
        context_claims.append(
            {
                "claim_id": claim.claim_id,
                "claim_type": claim.claim_type,
                "importance": claim.importance,
                "rendered_text": disposition.rendered_text,
                "verification": {
                    "status": verification.status,
                    "reason": verification.reason,
                },
                "evidence_ids": sorted(claim.evidence_ids),
                "counter_evidence_ids": sorted(claim.counter_evidence_ids),
            }
        )

    context_evidence = [
        _evidence_payload(evidence_by_id[evidence_id])
        for evidence_id in sorted(allowed_ids)
    ]
    payload = {
        "schema_version": "1.0",
        "purpose": "scholartrace-b3-b4-private-evidence-input",
        "question_id": question_id,
        "paper_pool_sha256": paper_pool_sha256,
        "claims": context_claims,
        "evidence": context_evidence,
        "blocked_critical_claim_ids": sorted(report_gate.blocked_critical_claim_ids),
        "rules": {
            "scholargraph_is_abstract_only": True,
            "only_listed_evidence_ids_are_citable": True,
            "unsupported_claims_excluded": True,
        },
    }
    context = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(context) > max_context_characters:
        raise EvaluationInputError("private Evidence context exceeds the frozen limit")
    frozen_ids = frozenset(allowed_ids)
    return PreparedEvidenceInput(
        evidence_context=context,
        allowed_evidence_ids=frozen_ids,
        input_sha256=evidence_input_sha256(context, frozen_ids),
        included_claim_count=len(context_claims),
        evidence_count=len(context_evidence),
    )


def _unique_by_id(
    rows: list[T],
    identity_of: Callable[[T], str],
    label: str,
) -> dict[str, T]:
    indexed: dict[str, T] = {}
    for row in rows:
        identity = identity_of(row)
        if not identity:
            raise EvaluationInputError(f"{label} has no valid identity")
        if identity in indexed:
            raise EvaluationInputError(f"duplicate {label} identity: {identity}")
        indexed[identity] = row
    return indexed


def _evidence_payload(evidence: Evidence) -> dict[str, object]:
    return {
        "evidence_id": evidence.evidence_id,
        "canonical_paper_id": evidence.canonical_paper_id,
        "quote": evidence.quote,
        "evidence_level": evidence.evidence_level,
        "page_number": evidence.page_number,
        "section": evidence.section,
        "content_sha256": evidence.content_sha256,
        "source_sha256": evidence.source_sha256,
    }
