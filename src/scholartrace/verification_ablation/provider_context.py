"""Provider-side context compaction that preserves evidence and Claim bindings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from scholartrace.model_provider.plan_generator import ApiCallCounter

from .models import (
    AblationGeneratedReport,
    AblationReportGenerator,
    AblationReportRequest,
    ProviderProtocol,
)

COMPACT_CONTEXT_ID = "sp04-provider-context-compact-v1"


def compact_prepared_context(prepared_context: str) -> str:
    """Remove transport metadata while retaining all report-eligible semantics."""

    payload = json.loads(prepared_context)
    claims = []
    for claim in payload.get("claims", []):
        claims.append(
            {
                "claim_id": str(claim["claim_id"]),
                "text": str(claim["text"]),
                "evidence_ids": claim.get("evidence_ids", []),
                "counter_evidence_ids": claim.get("counter_evidence_ids", []),
                "prepared_semantic_status": str(claim["prepared_semantic_status"]),
                "included": claim["included"],
                "marker": claim.get("marker"),
            }
        )
    evidence = []
    for item in payload.get("evidence", []):
        evidence.append(
            {
                "evidence_id": str(item["evidence_id"]),
                "canonical_paper_id": str(item["canonical_paper_id"]),
                "quote": str(item["quote"]),
                "section": item.get("section"),
                "page_number": item.get("page_number"),
            }
        )
    compact: dict[str, Any] = {
        "schema_version": payload.get("schema_version", "1.0"),
        "purpose": COMPACT_CONTEXT_ID,
        "question_id": payload["question_id"],
        "question": payload["question"],
        "variant": payload["variant"],
        "frozen_input_sha256": payload["frozen_input_sha256"],
        "claims": claims,
        "evidence": evidence,
        "rules": payload.get("rules", {}),
    }
    return json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(slots=True)
class CompactReportContextGenerator:
    """Wrap a strict generator without changing its model or output contract."""

    inner: AblationReportGenerator

    @property
    def call_counter(self) -> ApiCallCounter | None:
        """Expose the same counter to the runner pre-dispatch budget guard."""
        return cast(ApiCallCounter | None, getattr(self.inner, "call_counter", None))

    @property
    def model_profile(self) -> str:
        return self.inner.model_profile

    @property
    def model_identifier(self) -> str:
        return self.inner.model_identifier

    @property
    def provider_protocol(self) -> ProviderProtocol:
        return self.inner.provider_protocol

    @property
    def prompt_template_sha256(self) -> str:
        return self.inner.prompt_template_sha256

    @property
    def report_schema_sha256(self) -> str:
        return self.inner.report_schema_sha256

    async def generate(self, request: AblationReportRequest) -> AblationGeneratedReport:
        compact_request = AblationReportRequest(
            question_id=request.question_id,
            split=request.split,
            question=request.question,
            variant=request.variant,
            frozen_input_sha256=request.frozen_input_sha256,
            evidence_identity_sha256=request.evidence_identity_sha256,
            configuration_sha256=request.configuration_sha256,
            report_length_limit_chars=request.report_length_limit_chars,
            prepared_context=compact_prepared_context(request.prepared_context),
            allowed_evidence_ids=request.allowed_evidence_ids,
            dispositions=request.dispositions,
        )
        return await self.inner.generate(compact_request)
