"""Deterministic, bounded Claim selection for the stage-two candidate path."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import Field, model_validator

from scholartrace.contracts import Claim

from .models import AblationFrozenInput, AblationModel, canonical_sha256

RULE_ID = "sp03-selective-verification-v1"
_RULE_DEFINITION = {
    "rule_id": RULE_ID,
    "selected_when": [
        "claim_type_is_comparison_or_inference",
        "origin_is_cross_paper_synthesis",
        "claim_has_counter_evidence",
        "claim_has_multiple_evidence_items",
        "claim_importance_is_critical",
    ],
    "selection_is_deterministic": True,
    "selection_uses_no_model_call": True,
    "unselected_state": "unverified",
}
SELECTION_RULE_SHA256 = canonical_sha256(_RULE_DEFINITION)


class SelectionDecision(AblationModel):
    claim_id: str
    selected: bool
    reasons: list[str]


class SelectionPlan(AblationModel):
    """Selection output bound to one frozen input and one rule identity."""

    schema_version: Literal["1.0"] = "1.0"
    purpose: Literal["scholartrace-sp03-selection-plan"] = "scholartrace-sp03-selection-plan"
    rule_id: str
    rule_sha256: str
    question_id: str
    frozen_input_sha256: str
    total_claim_count: int = Field(ge=0)
    selected_claim_ids: list[str]
    skipped_claim_ids: list[str]
    decisions: list[SelectionDecision]

    @model_validator(mode="after")
    def validate_plan(self) -> SelectionPlan:
        claim_ids = {item.claim_id for item in self.decisions}
        if len(claim_ids) != len(self.decisions):
            raise ValueError("selection plan contains duplicate Claim IDs")
        if len(self.decisions) != self.total_claim_count:
            raise ValueError("selection plan Claim coverage is incomplete")
        selected = {item.claim_id for item in self.decisions if item.selected}
        skipped = {item.claim_id for item in self.decisions if not item.selected}
        if selected != set(self.selected_claim_ids) or skipped != set(self.skipped_claim_ids):
            raise ValueError("selection plan summary does not match decisions")
        if selected & skipped or selected | skipped != claim_ids:
            raise ValueError("selection plan selected/skipped sets are inconsistent")
        return self


def _decision_reasons(claim: Claim) -> list[str]:
    reasons: list[str] = []
    if claim.claim_type in {"comparison", "inference"}:
        reasons.append("claim_type_high_risk")
    if claim.origin == "cross_paper_synthesis":
        reasons.append("cross_paper_synthesis")
    if claim.counter_evidence_ids:
        reasons.append("counter_evidence_present")
    if len(claim.evidence_ids) > 1:
        reasons.append("multiple_evidence_items")
    if claim.importance == "critical":
        reasons.append("critical_importance")
    return reasons


def selection_decision(claim: Claim) -> SelectionDecision:
    reasons = _decision_reasons(claim)
    return SelectionDecision(
        claim_id=claim.claim_id,
        selected=bool(reasons),
        reasons=reasons,
    )


def build_selection_plan(frozen: AblationFrozenInput) -> SelectionPlan:
    decisions = [
        selection_decision(claim)
        for claim in sorted(frozen.claims, key=lambda item: item.claim_id)
    ]
    return SelectionPlan(
        rule_id=RULE_ID,
        rule_sha256=SELECTION_RULE_SHA256,
        question_id=frozen.question_id,
        frozen_input_sha256=frozen.input_sha256,
        total_claim_count=len(decisions),
        selected_claim_ids=[item.claim_id for item in decisions if item.selected],
        skipped_claim_ids=[item.claim_id for item in decisions if not item.selected],
        decisions=decisions,
    )


def should_verify(frozen: AblationFrozenInput, claim: Claim) -> bool:
    """Return the deterministic candidate decision for one Claim."""

    if claim.claim_id not in {item.claim_id for item in frozen.claims}:
        raise ValueError(f"unknown Claim for {frozen.question_id}: {claim.claim_id}")
    return selection_decision(claim).selected


VerificationSelector = Callable[[AblationFrozenInput, Claim], bool]
