"""Profile-gated semantic Verifier execution over validated evidence only."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal, Protocol

from scholartrace.contracts import (
    Claim,
    Evidence,
    ModelRoutingPolicy,
    Verification,
)
from scholartrace.verification.models import ClaimValidation, SemanticVerificationDraft

VerifierKind = Literal["model", "human", "fixture"]


class SemanticVerifierBackend(Protocol):
    verifier_kind: VerifierKind
    profile_id: str | None

    async def verify(
        self,
        *,
        claim: Claim,
        evidence: list[Evidence],
    ) -> SemanticVerificationDraft: ...


class VerifierUnavailableError(RuntimeError):
    """The configured critical Verifier route is unavailable or disabled."""


class VerifierProfileGate:
    @staticmethod
    def assert_allowed(
        *,
        backend: SemanticVerifierBackend,
        policy: ModelRoutingPolicy | None,
        allow_fixture: bool,
    ) -> None:
        if backend.verifier_kind == "fixture":
            if not allow_fixture:
                raise VerifierUnavailableError("fixture Verifier is disabled outside fixture mode")
            return
        if backend.verifier_kind == "human":
            return
        if policy is None or backend.profile_id is None:
            raise VerifierUnavailableError("critical Verifier model policy is not configured")
        route = next(
            (route for route in policy.routes if route.node == "critical_verifier"),
            None,
        )
        if route is None or route.default_profile_id != backend.profile_id:
            raise VerifierUnavailableError("critical Verifier backend does not match its route")
        profile = next(
            (item for item in policy.profiles if item.profile_id == backend.profile_id),
            None,
        )
        if profile is None or not profile.enabled:
            raise VerifierUnavailableError("critical Verifier profile is disabled")
        if profile.kind == "api" and not policy.paid_routes_enabled:
            raise VerifierUnavailableError("paid critical Verifier routes are disabled")


class VerifierRunner:
    def __init__(
        self,
        *,
        backend: SemanticVerifierBackend,
        policy: ModelRoutingPolicy | None,
        allow_fixture: bool = False,
    ) -> None:
        self.backend = backend
        self.policy = policy
        self.allow_fixture = allow_fixture

    async def verify(
        self,
        *,
        claims: list[Claim],
        evidence: list[Evidence],
        validations: list[ClaimValidation],
        verified_at: datetime,
    ) -> list[Verification]:
        validation_by_claim = {item.claim_id: item for item in validations}
        if len(validation_by_claim) != len(validations):
            raise ValueError("Verifier received duplicate validation results")
        evidence_by_id = {item.evidence_id: item for item in evidence}
        results: list[Verification] = []
        profile_checked = False
        for claim in sorted(claims, key=lambda item: item.claim_id):
            validation = validation_by_claim.get(claim.claim_id)
            if validation is None:
                raise ValueError(f"claim has no deterministic validation: {claim.claim_id}")
            if not validation.passed:
                issue_codes = sorted({issue.code for issue in validation.issues})
                results.append(
                    self._verification(
                        claim=claim,
                        validation=validation,
                        draft=SemanticVerificationDraft(
                            status="unsupported",
                            reason="Deterministic validation failed: " + ", ".join(issue_codes),
                            recommended_action=(
                                "follow_up" if claim.importance == "critical" else "remove"
                            ),
                        ),
                        verifier="deterministic",
                        verified_at=verified_at,
                    )
                )
                continue
            if not profile_checked:
                VerifierProfileGate.assert_allowed(
                    backend=self.backend,
                    policy=self.policy,
                    allow_fixture=self.allow_fixture,
                )
                profile_checked = True
            supplied = [
                evidence_by_id[evidence_id]
                for evidence_id in validation.checked_evidence_ids
                if evidence_id in evidence_by_id
            ]
            draft = await self.backend.verify(claim=claim, evidence=supplied)
            if draft.status == "conflicted" and not claim.counter_evidence_ids:
                raise ValueError("conflicted verification requires explicit counter-evidence")
            results.append(
                self._verification(
                    claim=claim,
                    validation=validation,
                    draft=draft,
                    verifier=self.backend.verifier_kind,
                    verified_at=verified_at,
                )
            )
        return results

    @staticmethod
    def _verification(
        *,
        claim: Claim,
        validation: ClaimValidation,
        draft: SemanticVerificationDraft,
        verifier: Literal["deterministic", "model", "human", "fixture"],
        verified_at: datetime,
    ) -> Verification:
        digest = hashlib.sha256(
            "\0".join(
                (
                    claim.claim_id,
                    validation.validation_id,
                    draft.status,
                    draft.reason,
                    verifier,
                )
            ).encode()
        ).hexdigest()
        return Verification(
            verification_id=f"verification:m4:{digest[:24]}",
            claim_id=claim.claim_id,
            status=draft.status,
            checked_evidence_ids=validation.checked_evidence_ids,
            reason=draft.reason,
            recommended_action=draft.recommended_action,
            verifier=verifier,
            verified_at=verified_at,
        )
