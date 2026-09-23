"""Freeze a separate stage-two question set from previously validated evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import Field

from scholartrace.contracts import Claim, DocuMindBinding, Evidence, Paper
from scholartrace.evidence.models import RetrievalChunk
from scholartrace.verification.validator import EvidenceValidator

from .models import (
    AblationFrozenInput,
    AblationModel,
    AblationSourceRef,
    canonical_sha256,
)


class StageTwoQuestionSpec(AblationModel):
    """One new question and its pre-registered source Claim selection."""

    question_id: str = Field(min_length=1)
    question: str = Field(min_length=10)
    categories: list[str] = Field(min_length=1)
    source_question_claims: dict[str, list[int]] = Field(min_length=1)
    key_points: list[str] = Field(min_length=1)
    review_focus: str = Field(min_length=1)


def _merge_papers(sources: Sequence[AblationFrozenInput]) -> list[Paper]:
    merged: dict[str, Paper] = {}
    for source in sources:
        for paper in source.papers:
            merged.setdefault(paper.canonical_paper_id, paper)
    return [merged[key] for key in sorted(merged)]


def _merge_bindings(sources: Sequence[AblationFrozenInput]) -> list[DocuMindBinding]:
    merged: dict[str, DocuMindBinding] = {}
    for source in sources:
        for binding in source.bindings:
            merged.setdefault(binding.canonical_paper_id, binding)
    return [merged[key] for key in sorted(merged)]


def _merge_chunks(
    sources: Sequence[AblationFrozenInput], paper_ids: set[str]
) -> dict[str, list[RetrievalChunk]]:
    merged: dict[str, dict[str, RetrievalChunk]] = {}
    for source in sources:
        for paper_id, chunks in source.chunks_by_paper.items():
            if paper_id not in paper_ids:
                continue
            bucket = merged.setdefault(paper_id, {})
            for chunk in chunks:
                bucket.setdefault(chunk.chunk_id, chunk)
    return {
        paper_id: [bucket[key] for key in sorted(bucket)]
        for paper_id, bucket in sorted(merged.items())
    }


def derive_frozen_input(
    *,
    source_inputs: Mapping[str, AblationFrozenInput],
    spec: StageTwoQuestionSpec,
    validated_at: datetime,
) -> AblationFrozenInput:
    """Create a new deterministic input without carrying semantic-verifier state."""

    sources: list[AblationFrozenInput] = []
    selected_claims: list[Claim] = []
    selected_evidence_ids: set[str] = set()
    selected_counter_evidence_ids: set[str] = set()
    seen_sources: set[str] = set()

    for source_id, indexes in spec.source_question_claims.items():
        source = source_inputs.get(source_id)
        if source is None:
            raise ValueError(f"unknown source question: {source_id}")
        if source_id not in seen_sources:
            sources.append(source)
            seen_sources.add(source_id)
        if not indexes:
            raise ValueError(f"{spec.question_id}: source selection is empty: {source_id}")
        for index in indexes:
            if index < 0 or index >= len(source.claims):
                raise ValueError(
                    f"{spec.question_id}: claim index out of range: {source_id}[{index}]"
                )
            original = source.claims[index]
            selected_claims.append(
                original.model_copy(
                    update={"claim_id": f"claim:{spec.question_id}:{len(selected_claims) + 1:02d}"}
                )
            )
            selected_evidence_ids.update(original.evidence_ids)
            selected_counter_evidence_ids.update(original.counter_evidence_ids)

    if not selected_claims:
        raise ValueError(f"{spec.question_id}: no Claims selected")
    selected_evidence_ids.update(selected_counter_evidence_ids)

    evidence_by_id: dict[str, Evidence] = {}
    for source in sources:
        for evidence_item in source.evidence:
            evidence_by_id.setdefault(evidence_item.evidence_id, evidence_item)
    missing_evidence = selected_evidence_ids - set(evidence_by_id)
    if missing_evidence:
        raise ValueError(f"{spec.question_id}: missing Evidence: {sorted(missing_evidence)}")
    selected_evidence = [evidence_by_id[key] for key in sorted(selected_evidence_ids)]
    paper_ids = {item.canonical_paper_id for item in selected_evidence}
    papers = _merge_papers(sources)
    papers = [paper for paper in papers if paper.canonical_paper_id in paper_ids]
    bindings = [
        binding for binding in _merge_bindings(sources) if binding.canonical_paper_id in paper_ids
    ]
    chunks_by_paper = _merge_chunks(sources, paper_ids)
    if {paper.canonical_paper_id for paper in papers} != paper_ids:
        raise ValueError(f"{spec.question_id}: source papers do not cover Evidence")
    if set(chunks_by_paper) != paper_ids:
        raise ValueError(f"{spec.question_id}: source chunks do not cover Evidence papers")

    validation = EvidenceValidator().validate(
        claims=selected_claims,
        evidence=selected_evidence,
        papers=papers,
        bindings=bindings,
        chunks_by_paper=chunks_by_paper,
        validated_at=validated_at,
    )
    if validation.outcome != "succeeded" or any(not item.passed for item in validation.results):
        raise ValueError(f"{spec.question_id}: derived deterministic validation failed")

    source_report_hashes = sorted(source.source.report_sha256 for source in sources)
    source_content_hashes = sorted(source.source.report_content_sha256 for source in sources)
    derived_source = AblationSourceRef(
        artifact_id=f"sp02-derived:{spec.question_id}",
        report_path="agent/verification-ablation/SA-02/frozen_inputs.json",
        report_id=f"sp02-derived-report:{spec.question_id}",
        report_question=spec.question,
        report_sha256=canonical_sha256(source_report_hashes),
        report_content_sha256=canonical_sha256(source_content_hashes),
    )
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "purpose": "scholartrace-sa02-pre-semantic-verification-input",
        "question_id": spec.question_id,
        "split": "formal",
        "question": spec.question,
        "categories": spec.categories,
        "source": derived_source.model_dump(mode="json"),
        "papers": [item.model_dump(mode="json") for item in papers],
        "bindings": [item.model_dump(mode="json") for item in bindings],
        "chunks_by_paper": {
            paper_id: [item.model_dump(mode="json") for item in chunks]
            for paper_id, chunks in chunks_by_paper.items()
        },
        "claims": [item.model_dump(mode="json") for item in selected_claims],
        "evidence": [item.model_dump(mode="json") for item in selected_evidence],
        "deterministic_validation": validation.model_dump(mode="json"),
        "semantic_verification_results_included": False,
    }
    payload["input_sha256"] = canonical_sha256(payload)
    return AblationFrozenInput.model_validate(payload)


def build_stage_two_inputs(
    *,
    source_inputs: Mapping[str, AblationFrozenInput],
    specs: Sequence[StageTwoQuestionSpec],
    validated_at: datetime,
) -> dict[str, AblationFrozenInput]:
    """Build and validate the complete new question set."""

    if len({spec.question_id for spec in specs}) != len(specs):
        raise ValueError("stage-two question IDs must be unique")
    result = {
        spec.question_id: derive_frozen_input(
            source_inputs=source_inputs,
            spec=spec,
            validated_at=validated_at,
        )
        for spec in specs
    }
    if not result:
        raise ValueError("stage-two question set cannot be empty")
    return dict(sorted(result.items()))
