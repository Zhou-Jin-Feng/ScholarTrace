from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from scholartrace.scholargraph.real_miss import (
    M9_PARQUET_BUNDLE_SHA256,
    Eligibility,
    EligibilityReason,
    GoldBasisKind,
    GraphPathStatus,
    M9CaptureSubmission,
    M9GoldBasis,
    M9ObservationError,
    M9P0Store,
    M9ReviewSubmission,
    P0Decision,
    QueryStratum,
    ReviewStatus,
    RootCause,
    SampleOrigin,
    build_m9_b5_snapshot,
    m9_b5_config_sha256,
    m9_question_sha256,
    require_project_agent_path,
)


def _snapshot(question: str, candidates: list[str] | None = None):
    candidate_ids = candidates if candidates is not None else ["W1"]
    return build_m9_b5_snapshot(
        question=question,
        status="succeeded" if candidate_ids else "no_match",
        reason="matched" if candidate_ids else "no_catalog_match",
        candidate_openalex_ids=candidate_ids,
        parquet_bundle_sha256=M9_PARQUET_BUNDLE_SHA256,
    )


def _capture(
    index: int,
    *,
    origin: SampleOrigin = SampleOrigin.REAL,
    eligible: bool = True,
) -> M9CaptureSubmission:
    question = f"Which RAG method is used in unique case {index}?"
    if eligible:
        return M9CaptureSubmission(
            source_event_id=f"task:m9:case-{index}",
            sample_origin=origin,
            question=question,
            eligibility=Eligibility.GRAPH_ELIGIBLE,
            eligibility_reason=EligibilityReason.WITHIN_FROZEN_CORPUS_SCOPE,
            stratum=QueryStratum.ALIAS_BRIDGE,
            b5_snapshot=_snapshot(question),
        )
    return M9CaptureSubmission(
        source_event_id=f"task:m9:case-{index}",
        sample_origin=origin,
        question=question,
        eligibility=Eligibility.INELIGIBLE,
        eligibility_reason=EligibilityReason.OUTSIDE_TOPIC,
    )


def _review(
    record_id: str,
    *,
    gold: str,
    revision: int = 0,
    root_cause: RootCause | None = None,
    in_frozen_corpus: bool = True,
) -> M9ReviewSubmission:
    path_status = GraphPathStatus.NOT_APPLICABLE
    if root_cause == RootCause.G1_ALIAS:
        path_status = GraphPathStatus.PATH_MISSING
    elif root_cause == RootCause.G3_RANKING:
        path_status = GraphPathStatus.PATH_FOUND
    return M9ReviewSubmission(
        record_id=record_id,
        expected_revision=revision,
        status=ReviewStatus.CONFIRMED,
        gold_candidate_ids=[gold],
        gold_in_frozen_corpus_ids=[gold] if in_frozen_corpus else [],
        gold_basis=[
            M9GoldBasis(
                openalex_id=gold,
                kind=GoldBasisKind.OPENALEX_METADATA,
                authority_sha256="b" * 64,
            )
        ],
        graph_path_status=path_status,
        root_cause=root_cause,
        notes="private reviewer note",
    )


def _store(tmp_path: Path) -> M9P0Store:
    root = tmp_path / "project"
    return M9P0Store(root / "agent" / "m9-p0" / "observations.sqlite", project_root=root)


def test_snapshot_is_self_validating_and_bound_to_question() -> None:
    question = "Which RAG method uses a verified relation?"
    snapshot = _snapshot(question)
    assert snapshot.config_sha256 == m9_b5_config_sha256()
    assert snapshot.question_sha256 == m9_question_sha256(question.upper())
    payload = snapshot.model_dump(mode="json")
    payload["candidate_openalex_ids"] = ["W2"]
    with pytest.raises(ValidationError, match="snapshot hash"):
        type(snapshot).model_validate(payload)
    with pytest.raises(ValidationError, match="status and reason"):
        build_m9_b5_snapshot(
            question=question,
            status="succeeded",
            reason="no_catalog_match",
            candidate_openalex_ids=["W1"],
            parquet_bundle_sha256=M9_PARQUET_BUNDLE_SHA256,
        )
    with pytest.raises(ValidationError, match="captured question"):
        M9CaptureSubmission(
            source_event_id="task:m9:wrong-question",
            question="A different graph eligible RAG question",
            eligibility=Eligibility.GRAPH_ELIGIBLE,
            eligibility_reason=EligibilityReason.WITHIN_FROZEN_CORPUS_SCOPE,
            stratum=QueryStratum.RELATION_BRIDGE,
            b5_snapshot=snapshot,
        )


def test_capture_is_ordered_idempotent_and_rejects_duplicates(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        first_submission = _capture(1)
        first = store.capture(first_submission)
        repeated = store.capture(first_submission)
        second = store.capture(_capture(2, eligible=False))
        assert first.record_id == repeated.record_id
        assert [first.sequence_id, second.sequence_id] == [1, 2]
        assert store.verify_integrity()

        changed = first_submission.model_copy(
            update={"eligibility_reason": EligibilityReason.AMBIGUOUS}
        )
        with pytest.raises(M9ObservationError, match="different content"):
            store.capture(changed)
        duplicate_question = first_submission.model_copy(
            update={"source_event_id": "task:m9:another-event"}
        )
        with pytest.raises(M9ObservationError, match="duplicate normalized question"):
            store.capture(duplicate_question)
    finally:
        store.close()


def test_review_is_append_only_and_cross_validates_b5(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        observation = store.capture(_capture(1))
        first = store.review(
            _review(observation.record_id, gold="W2", root_cause=RootCause.G1_ALIAS)
        )
        assert first.revision == 1
        with pytest.raises(M9ObservationError, match="revision conflict"):
            store.review(
                _review(observation.record_id, gold="W2", root_cause=RootCause.G1_ALIAS)
            )
        corrected = store.review(
            _review(
                observation.record_id,
                gold="W2",
                revision=1,
                root_cause=RootCause.G3_RANKING,
            )
        )
        assert corrected.revision == 2
        assert store.verify_integrity()

        covered = store.capture(_capture(2))
        with pytest.raises(M9ObservationError, match="B5-covered"):
            store.review(
                _review(covered.record_id, gold="W1", root_cause=RootCause.G1_ALIAS)
            )
    finally:
        store.close()


def test_public_report_excludes_private_content_and_fixtures(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        fixture = store.capture(_capture(1, origin=SampleOrigin.FIXTURE))
        store.review(_review(fixture.record_id, gold="W1"))
        real = store.capture(_capture(2))
        store.review(_review(real.record_id, gold="W2", root_cause=RootCause.G1_ALIAS))

        report = store.public_report()
        serialized = report.model_dump_json()
        assert report.decision == P0Decision.COLLECT_MORE
        assert report.counts.real_observations == 1
        assert report.counts.fixture_observations_excluded == 1
        assert [item.record_id for item in report.records] == [real.record_id]
        assert "Which RAG method" not in serialized
        assert "private reviewer note" not in serialized
        assert "task:m9:" not in serialized
    finally:
        store.close()


def test_public_report_counts_graph_and_non_graph_root_causes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        graph_miss = store.capture(_capture(1))
        corpus_miss = store.capture(_capture(2))
        store.review(
            _review(graph_miss.record_id, gold="W2", root_cause=RootCause.G1_ALIAS)
        )
        store.review(
            _review(
                corpus_miss.record_id,
                gold="W3",
                root_cause=RootCause.N1_CORPUS_GAP,
                in_frozen_corpus=False,
            )
        )

        report = store.public_report()
        assert report.root_cause_counts == {"G1_ALIAS": 1, "N1_CORPUS_GAP": 1}
        assert report.counts.graph_fixable_misses == 1
        assert report.counts.frozen_corpus_miss_opportunities == 1
    finally:
        store.close()


def test_gate_requires_real_window_misses_and_two_graph_causes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        for index in range(30):
            observation = store.capture(_capture(index))
            if index < 8:
                cause = RootCause.G1_ALIAS if index % 2 == 0 else RootCause.G3_RANKING
                review = _review(observation.record_id, gold=f"W{index + 2}", root_cause=cause)
            else:
                review = _review(observation.record_id, gold="W1")
            store.review(review)

        report = store.public_report()
        assert report.decision == P0Decision.GO_IMPLEMENT
        assert report.counts.eligible_real_observations == 30
        assert report.counts.confirmed_b5_misses == 8
        assert report.counts.frozen_corpus_miss_opportunities == 8
        assert report.counts.graph_fixable_misses == 8
        assert report.counts.distinct_graph_root_causes == 2
        assert report.criteria.model_dump(mode="python") == {
            "initial_window_complete": True,
            "all_eligible_reviewed": True,
            "minimum_confirmed_misses": True,
            "minimum_corpus_opportunities": True,
            "minimum_graph_fixable_misses": True,
            "minimum_graph_root_causes": True,
            "capture_chain_valid": True,
            "all_included_records_reproducible": True,
            "fixtures_excluded_from_gate": True,
        }
    finally:
        store.close()


def test_fifty_reviewed_eligible_without_need_is_no_go(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        for index in range(50):
            observation = store.capture(_capture(index))
            store.review(_review(observation.record_id, gold="W1"))
        report = store.public_report()
        assert report.decision == P0Decision.NO_GO_INSUFFICIENT_NEED
        with pytest.raises(M9ObservationError, match="50-question"):
            store.capture(_capture(51))
        with store.connection:
            store.connection.execute(
                "UPDATE observations SET question = ? WHERE record_id = ?",
                ("tampered after completed collection", "m9-p0-000001"),
            )
        assert store.public_report().decision == P0Decision.COLLECT_MORE
    finally:
        store.close()


def test_integrity_detects_private_database_tampering(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        observation = store.capture(_capture(1))
        store.review(_review(observation.record_id, gold="W1"))
        with store.connection:
            store.connection.execute(
                "UPDATE observations SET question = ? WHERE record_id = ?",
                ("tampered private question", observation.record_id),
            )
        assert not store.verify_integrity()
        report = store.public_report()
        assert not report.criteria.capture_chain_valid
        assert report.decision == P0Decision.COLLECT_MORE
    finally:
        store.close()


def test_private_database_and_inputs_must_stay_under_project_agent(tmp_path: Path) -> None:
    root = tmp_path / "project"
    assert require_project_agent_path(
        root / "agent" / "m9-p0" / "input.json", project_root=root
    ).is_absolute()
    with pytest.raises(M9ObservationError, match="below the project agent"):
        M9P0Store(root / "artifacts" / "observations.sqlite", project_root=root)


def test_rejected_review_cannot_smuggle_private_or_gold_fields() -> None:
    with pytest.raises(ValidationError, match="cannot carry"):
        M9ReviewSubmission(
            record_id="m9-p0-000001",
            expected_revision=0,
            status=ReviewStatus.REJECTED,
            notes="should not be retained",
        )


def test_public_report_json_is_parseable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    output = tmp_path / "public" / "m9.json"
    try:
        digest = store.write_public_report(output)
        payload = json.loads(output.read_text("utf-8"))
        assert payload["decision"] == "COLLECT_MORE"
        assert payload["records"] == []
        assert len(digest) == 64
    finally:
        store.close()


def test_review_history_hash_tampering_is_detected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        observation = store.capture(_capture(1))
        store.review(_review(observation.record_id, gold="W1"))
        with sqlite3.connect(store.path) as external:
            external.execute(
                "UPDATE reviews SET review_sha256 = ? WHERE record_id = ?",
                ("c" * 64, observation.record_id),
            )
        assert not store.verify_integrity()
    finally:
        store.close()
