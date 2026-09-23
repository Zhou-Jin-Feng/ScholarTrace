from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sa04_synthetic import make_synthetic_inputs

from scholartrace.verification_ablation.stage_two_questions import (
    StageTwoQuestionSpec,
    build_stage_two_inputs,
)
from scholartrace.verification_ablation.stage_two_selection import (
    RULE_ID,
    SELECTION_RULE_SHA256,
    build_selection_plan,
    should_verify,
)

VALIDATED_AT = datetime(2026, 9, 22, tzinfo=UTC)


def _input():
    source_inputs = make_synthetic_inputs()
    spec = StageTwoQuestionSpec(
        question_id="sp03-test-01",
        question="What does the synthetic evidence support about the measured result?",
        categories=["ordinary_fact"],
        source_question_claims={"synthetic-sa04-pilot": [0, 1, 2]},
        key_points=["The result is tied to the supplied evidence."],
        review_focus="Keep the claim bounded to the supplied evidence.",
    )
    return build_stage_two_inputs(
        source_inputs=source_inputs,
        specs=[spec],
        validated_at=VALIDATED_AT,
    )["sp03-test-01"]


def test_selection_plan_is_deterministic_and_audits_every_claim() -> None:
    frozen = _input()
    plan = build_selection_plan(frozen)

    assert plan.rule_id == RULE_ID
    assert plan.rule_sha256 == SELECTION_RULE_SHA256
    assert len(plan.decisions) == len(frozen.claims)
    assert set(plan.selected_claim_ids) | set(plan.skipped_claim_ids) == {
        claim.claim_id for claim in frozen.claims
    }
    assert plan.selected_claim_ids
    assert plan.skipped_claim_ids
    assert should_verify(frozen, frozen.claims[1]) is True
    assert should_verify(frozen, frozen.claims[0]) is False


def test_selection_rejects_unknown_claim() -> None:
    frozen = _input()
    with pytest.raises(ValueError, match="unknown Claim"):
        should_verify(frozen, frozen.claims[0].model_copy(update={"claim_id": "missing"}))
