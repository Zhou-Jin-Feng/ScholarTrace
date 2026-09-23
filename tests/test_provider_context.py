from __future__ import annotations

import json

from sa04_synthetic import make_synthetic_inputs

from scholartrace.verification_ablation.models import AblationClaimDisposition
from scholartrace.verification_ablation.provider_context import (
    COMPACT_CONTEXT_ID,
    compact_prepared_context,
)
from scholartrace.verification_ablation.runner import prepare_variant_input


def test_compact_context_preserves_report_bindings_and_quotes() -> None:
    frozen = next(iter(make_synthetic_inputs().values()))
    dispositions = tuple(
        AblationClaimDisposition(
            claim_id=claim.claim_id,
            status="unverified",
            included=True,
            marker="[UNVERIFIED]",
        )
        for claim in frozen.claims
    )
    prepared = prepare_variant_input(
        frozen=frozen,
        variant="V-off",
        dispositions=dispositions,
        max_context_characters=40_000,
    )

    original = json.loads(prepared.prepared_context)
    compact = json.loads(compact_prepared_context(prepared.prepared_context))
    assert compact["purpose"] == COMPACT_CONTEXT_ID
    assert compact["question_id"] == original["question_id"]
    assert [item["claim_id"] for item in compact["claims"]] == [
        item["claim_id"] for item in original["claims"]
    ]
    assert [item["text"] for item in compact["claims"]] == [
        item["text"] for item in original["claims"]
    ]
    assert [item["evidence_id"] for item in compact["evidence"]] == [
        item["evidence_id"] for item in original["evidence"]
    ]
    assert [item["quote"] for item in compact["evidence"]] == [
        item["quote"] for item in original["evidence"]
    ]
    assert all("content_sha256" not in item for item in compact["evidence"])
