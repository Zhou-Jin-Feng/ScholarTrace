"""Final report policy for verified, partial, conflicted, and unsupported claims."""

from __future__ import annotations

from scholartrace.contracts import Claim, Verification
from scholartrace.verification.models import (
    ReportClaimDisposition,
    ReportGateResult,
)

STATUS_MARKERS = {
    "partially_supported": "[PARTIALLY SUPPORTED]",
    "conflicted": "[CONFLICTED]",
}


class VerificationReportGate:
    def apply(
        self,
        *,
        claims: list[Claim],
        verifications: list[Verification],
    ) -> ReportGateResult:
        verification_by_claim = {item.claim_id: item for item in verifications}
        if len(verification_by_claim) != len(verifications):
            raise ValueError("report gate received duplicate verifications")
        dispositions: list[ReportClaimDisposition] = []
        blocked_critical: list[str] = []
        for claim in sorted(claims, key=lambda item: item.claim_id):
            verification = verification_by_claim.get(claim.claim_id)
            status = verification.status if verification is not None else "unsupported"
            included = status != "unsupported"
            marker = STATUS_MARKERS.get(status)
            if claim.importance == "critical" and not included:
                blocked_critical.append(claim.claim_id)
            rendered = f"{marker} {claim.text}" if marker else claim.text
            dispositions.append(
                ReportClaimDisposition(
                    claim_id=claim.claim_id,
                    importance=claim.importance,
                    verification_status=status,
                    included=included,
                    marker=marker,
                    rendered_text=rendered,
                )
            )
        return ReportGateResult(
            dispositions=dispositions,
            blocked_critical_claim_ids=blocked_critical,
            report_safe=all(
                not item.included
                or item.verification_status == "supported"
                or item.marker is not None
                for item in dispositions
            ),
        )
