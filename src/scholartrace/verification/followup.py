"""Single-round, budget-limited policy for targeted evidence follow-up."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Literal

from scholartrace.contracts import Budget, Claim, FollowUpRequest, Verification

STATUS_PRIORITY = {
    "conflicted": 0,
    "unsupported": 1,
    "partially_supported": 2,
    "supported": 3,
}


class FollowUpPolicy:
    def select(
        self,
        *,
        task_id: str,
        claims: list[Claim],
        verifications: list[Verification],
        claim_subquestions: Mapping[str, str],
        budget: Budget,
        existing_requests: list[FollowUpRequest] | None = None,
        target_papers_by_claim: Mapping[str, list[str]] | None = None,
    ) -> list[FollowUpRequest]:
        existing = existing_requests or []
        if len(existing) > 1:
            raise ValueError("M4 permits at most one existing follow-up request")
        if existing:
            return []
        if budget.limits.max_rounds < 2:
            return []
        if budget.usage.queries >= budget.limits.max_queries:
            return []
        if budget.usage.api_calls >= budget.limits.max_api_calls:
            return []
        verification_by_claim = {item.claim_id: item for item in verifications}
        candidates: list[tuple[Claim, Verification]] = []
        for claim in claims:
            verification = verification_by_claim.get(claim.claim_id)
            if verification is None or verification.recommended_action != "follow_up":
                continue
            if verification.status == "supported":
                continue
            if claim.claim_id not in claim_subquestions:
                continue
            candidates.append((claim, verification))
        if not candidates:
            return []
        claim, verification = min(
            candidates,
            key=lambda item: (
                0 if item[0].importance == "critical" else 1,
                STATUS_PRIORITY[item[1].status],
                item[0].claim_id,
            ),
        )
        target_papers = sorted(
            set((target_papers_by_claim or {}).get(claim.claim_id, []))
        )[:20]
        digest = hashlib.sha256(
            "\0".join(
                (
                    task_id,
                    claim.claim_id,
                    claim_subquestions[claim.claim_id],
                    verification.status,
                    *target_papers,
                )
            ).encode()
        ).hexdigest()
        return [
            FollowUpRequest(
                request_id=f"follow-up:m4:{digest[:24]}",
                task_id=task_id,
                claim_id=claim.claim_id,
                subquestion_id=claim_subquestions[claim.claim_id],
                reason=verification.reason,
                missing_evidence_type=self._evidence_type(verification),
                target_paper_ids=target_papers,
            )
        ]

    @staticmethod
    def _evidence_type(
        verification: Verification,
    ) -> Literal["fulltext", "counter_evidence", "experimental_setup", "citation_path"]:
        if "citation_edge_missing" in verification.reason:
            return "citation_path"
        if verification.status == "conflicted":
            return "counter_evidence"
        if verification.status == "partially_supported":
            return "experimental_setup"
        return "fulltext"
