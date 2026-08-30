"""Deterministic Claim-Evidence validation before any semantic model call."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from scholartrace.citations.models import (
    CitationGraphArtifact,
    CitationPaperLifecycle,
)
from scholartrace.contracts import Claim, DocuMindBinding, Evidence, Paper
from scholartrace.evidence.models import RetrievalChunk
from scholartrace.verification.models import (
    CitationRequirement,
    ClaimValidation,
    ValidationBundle,
    ValidationIssue,
    ValidationIssueCode,
)

NUMBER_PATTERN = re.compile(
    r"(?<![\w.])[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?(?:\s*%)?"
)


class EvidenceValidator:
    def validate(
        self,
        *,
        claims: list[Claim],
        evidence: list[Evidence],
        papers: list[Paper],
        bindings: list[DocuMindBinding],
        chunks_by_paper: Mapping[str, list[RetrievalChunk]],
        validated_at: datetime,
        citation_graph: CitationGraphArtifact | None = None,
        citation_requirements: list[CitationRequirement] | None = None,
        lifecycle_records: list[CitationPaperLifecycle] | None = None,
    ) -> ValidationBundle:
        self._require_unique([claim.claim_id for claim in claims], "claim")
        self._require_unique([item.evidence_id for item in evidence], "evidence")
        self._require_unique([paper.canonical_paper_id for paper in papers], "paper")
        self._require_unique(
            [binding.canonical_paper_id for binding in bindings], "binding paper"
        )
        evidence_by_id = {item.evidence_id: item for item in evidence}
        paper_by_id = {paper.canonical_paper_id: paper for paper in papers}
        binding_by_id = {
            binding.canonical_paper_id: binding for binding in bindings
        }
        lifecycle_by_id = {
            record.canonical_paper_id: record
            for record in (lifecycle_records or [])
        }
        requirements_by_claim: dict[str, list[CitationRequirement]] = {}
        for requirement in citation_requirements or []:
            requirements_by_claim.setdefault(requirement.claim_id, []).append(requirement)
        graph_edges = (
            {
                (edge.citing_paper_id, edge.cited_paper_id)
                for edge in citation_graph.edges
            }
            if citation_graph is not None
            else set()
        )

        results = [
            self._validate_claim(
                claim=claim,
                evidence_by_id=evidence_by_id,
                paper_by_id=paper_by_id,
                binding_by_id=binding_by_id,
                chunks_by_paper=chunks_by_paper,
                lifecycle_by_id=lifecycle_by_id,
                requirements=requirements_by_claim.get(claim.claim_id, []),
                graph_edges=graph_edges,
                graph_supplied=citation_graph is not None,
                validated_at=validated_at,
            )
            for claim in sorted(claims, key=lambda item: item.claim_id)
        ]
        has_critical_error = any(
            not result.passed
            and next(claim for claim in claims if claim.claim_id == result.claim_id).importance
            == "critical"
            for result in results
        )
        has_error = any(not result.passed for result in results)
        outcome: Literal["succeeded", "degraded", "failed"] = (
            "failed" if has_critical_error else "degraded" if has_error else "succeeded"
        )
        digest = hashlib.sha256(
            "\0".join(
                f"{result.validation_id}:{result.passed}" for result in results
            ).encode()
        ).hexdigest()
        return ValidationBundle(
            bundle_id=f"validation-bundle:m4:{digest[:24]}",
            results=results,
            outcome=outcome,
            generated_at=validated_at,
        )

    def _validate_claim(
        self,
        *,
        claim: Claim,
        evidence_by_id: dict[str, Evidence],
        paper_by_id: dict[str, Paper],
        binding_by_id: dict[str, DocuMindBinding],
        chunks_by_paper: Mapping[str, list[RetrievalChunk]],
        lifecycle_by_id: dict[str, CitationPaperLifecycle],
        requirements: list[CitationRequirement],
        graph_edges: set[tuple[str, str]],
        graph_supplied: bool,
        validated_at: datetime,
    ) -> ClaimValidation:
        issues: list[ValidationIssue] = []
        referenced_ids = list(dict.fromkeys(claim.evidence_ids + claim.counter_evidence_ids))
        if not claim.evidence_ids:
            issues.append(
                self._issue(
                    code="claim_missing_evidence",
                    claim=claim,
                    message="claim has no supporting evidence reference",
                )
            )
        found: list[Evidence] = []
        for evidence_id in referenced_ids:
            item = evidence_by_id.get(evidence_id)
            if item is None:
                issues.append(
                    self._issue(
                        code="evidence_missing",
                        claim=claim,
                        evidence_id=evidence_id,
                        message="claim references evidence that does not exist",
                    )
                )
                continue
            found.append(item)
            issues.extend(
                self._validate_evidence(
                    claim=claim,
                    evidence=item,
                    paper_by_id=paper_by_id,
                    binding_by_id=binding_by_id,
                    chunks_by_paper=chunks_by_paper,
                    lifecycle_by_id=lifecycle_by_id,
                )
            )

        claim_numbers = self._numbers(claim.text)
        supporting_quotes = " ".join(
            evidence_by_id[evidence_id].quote
            for evidence_id in claim.evidence_ids
            if evidence_id in evidence_by_id
        )
        evidence_numbers = self._numbers(supporting_quotes)
        for number in sorted(claim_numbers - evidence_numbers):
            issues.append(
                self._issue(
                    code="numeric_mismatch",
                    claim=claim,
                    message=f"claim number {number!r} is absent from supporting evidence",
                )
            )

        for requirement in requirements:
            if not graph_supplied or (
                requirement.citing_paper_id,
                requirement.cited_paper_id,
            ) not in graph_edges:
                issues.append(
                    self._issue(
                        code="citation_edge_missing",
                        claim=claim,
                        paper_id=requirement.citing_paper_id,
                        message=(
                            "required explicit citation edge is absent: "
                            f"{requirement.citing_paper_id} -> {requirement.cited_paper_id}"
                        ),
                    )
                )

        ordered_issues = sorted(issues, key=lambda item: item.issue_id)
        checked = sorted(item.evidence_id for item in found)
        digest = hashlib.sha256(
            "\0".join(
                [claim.claim_id, *checked, *(issue.issue_id for issue in ordered_issues)]
            ).encode()
        ).hexdigest()
        return ClaimValidation(
            validation_id=f"validation:m4:{digest[:24]}",
            claim_id=claim.claim_id,
            passed=not any(issue.severity == "error" for issue in ordered_issues),
            checked_evidence_ids=checked,
            issues=ordered_issues,
            validated_at=validated_at,
        )

    def _validate_evidence(
        self,
        *,
        claim: Claim,
        evidence: Evidence,
        paper_by_id: dict[str, Paper],
        binding_by_id: dict[str, DocuMindBinding],
        chunks_by_paper: Mapping[str, list[RetrievalChunk]],
        lifecycle_by_id: dict[str, CitationPaperLifecycle],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        paper = paper_by_id.get(evidence.canonical_paper_id)
        if paper is None:
            issues.append(
                self._issue(
                    code="evidence_paper_missing",
                    claim=claim,
                    evidence=evidence,
                    message="evidence references a paper that does not exist",
                )
            )
            return issues
        if paper.version_of is not None and paper.version_of not in paper_by_id:
            issues.append(
                self._issue(
                    code="version_target_missing",
                    claim=claim,
                    evidence=evidence,
                    message="paper version_of target is absent from the validated paper set",
                )
            )
        lifecycle = lifecycle_by_id.get(paper.canonical_paper_id)
        if lifecycle is not None and lifecycle.stage != "analyzed":
            issues.append(
                self._issue(
                    code="lifecycle_incomplete",
                    claim=claim,
                    evidence=evidence,
                    message=f"citation-discovered paper lifecycle is {lifecycle.stage}",
                )
            )
        expected_content_hash = hashlib.sha256(evidence.quote.encode()).hexdigest()
        if evidence.content_sha256 != expected_content_hash:
            issues.append(
                self._issue(
                    code="content_hash_mismatch",
                    claim=claim,
                    evidence=evidence,
                    message="evidence quote hash does not match its content",
                )
            )
        if evidence.evidence_level != "fulltext":
            return issues
        binding = binding_by_id.get(evidence.canonical_paper_id)
        if binding is None:
            issues.append(
                self._issue(
                    code="binding_missing",
                    claim=claim,
                    evidence=evidence,
                    message="fulltext evidence has no active DocuMind binding",
                )
            )
            return issues
        comparisons: tuple[tuple[ValidationIssueCode, object, object, str], ...] = (
            (
                "document_key_mismatch",
                evidence.document_key,
                binding.document_key,
                "evidence document_key differs from the active binding",
            ),
            (
                "index_id_mismatch",
                evidence.index_id,
                binding.index_id,
                "evidence index_id differs from the active binding",
            ),
            (
                "source_hash_mismatch",
                evidence.source_sha256,
                binding.source_sha256,
                "evidence source hash differs from the active binding",
            ),
        )
        for code, actual, expected, message in comparisons:
            if actual != expected:
                issues.append(
                    self._issue(
                        code=code,
                        claim=claim,
                        evidence=evidence,
                        message=message,
                    )
                )
        chunks = {
            chunk.chunk_id: chunk
            for chunk in chunks_by_paper.get(evidence.canonical_paper_id, [])
        }
        chunk = chunks.get(evidence.chunk_id or "")
        if chunk is None:
            issues.append(
                self._issue(
                    code="chunk_missing",
                    claim=claim,
                    evidence=evidence,
                    message="evidence chunk is absent from the validated retrieval snapshot",
                )
            )
            return issues
        if evidence.chunk_content_sha256 != chunk.content_sha256:
            issues.append(
                self._issue(
                    code="chunk_hash_mismatch",
                    claim=claim,
                    evidence=evidence,
                    message="evidence chunk hash differs from the retrieval snapshot",
                )
            )
        if evidence.quote not in chunk.content:
            issues.append(
                self._issue(
                    code="quote_not_in_chunk",
                    claim=claim,
                    evidence=evidence,
                    message="evidence quote is not present in its referenced chunk",
                )
            )
        if evidence.page_number != chunk.page_number:
            issues.append(
                self._issue(
                    code="page_mismatch",
                    claim=claim,
                    evidence=evidence,
                    message="evidence page differs from the retrieval snapshot",
                )
            )
        if evidence.char_start is not None or evidence.char_end is not None:
            start = evidence.char_start
            end = evidence.char_end
            if (
                start is None
                or end is None
                or end > len(chunk.content)
                or chunk.content[start:end] != evidence.quote
            ):
                issues.append(
                    self._issue(
                        code="char_range_mismatch",
                        claim=claim,
                        evidence=evidence,
                        message="evidence character range does not resolve to its quote",
                    )
                )
        return issues

    @staticmethod
    def _issue(
        *,
        code: ValidationIssueCode,
        claim: Claim,
        message: str,
        evidence: Evidence | None = None,
        evidence_id: str | None = None,
        paper_id: str | None = None,
    ) -> ValidationIssue:
        resolved_evidence_id = evidence.evidence_id if evidence is not None else evidence_id
        resolved_paper_id = evidence.canonical_paper_id if evidence is not None else paper_id
        digest = hashlib.sha256(
            "\0".join(
                (
                    code,
                    claim.claim_id,
                    resolved_evidence_id or "",
                    resolved_paper_id or "",
                    message,
                )
            ).encode()
        ).hexdigest()
        return ValidationIssue(
            issue_id=f"validation-issue:m4:{digest[:24]}",
            code=code,
            severity="error",
            message=message,
            claim_id=claim.claim_id,
            evidence_id=resolved_evidence_id,
            paper_id=resolved_paper_id,
        )

    @staticmethod
    def _numbers(text: str) -> set[str]:
        return {
            match.group(0).replace(",", "").replace(" ", "").lower()
            for match in NUMBER_PATTERN.finditer(text)
        }

    @staticmethod
    def _require_unique(values: list[str], label: str) -> None:
        if len(values) != len(set(values)):
            raise ValueError(f"{label} IDs must be unique")
