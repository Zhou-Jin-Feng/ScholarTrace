from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scholartrace.scholargraph.prospective_gate import (
    M9_P2_PREREGISTRATION_SHA256,
    M9P2CaptureSubmission,
    M9P2ConditionSnapshot,
    M9P2Decision,
    M9P2Error,
    M9P2GoldSubmission,
    M9P2Store,
    m9_p2_question_sha256,
)
from scholartrace.scholargraph.real_miss import (
    Eligibility,
    EligibilityReason,
    GoldBasisKind,
    M9GoldBasis,
    QueryStratum,
    ReviewStatus,
)

ROOT = Path(__file__).resolve().parents[1]
PREREGISTRATION = ROOT / "evaluation" / "m9" / "p2_preregistration.json"


def _sha256_json(payload: object) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _store(tmp_path: Path) -> M9P2Store:
    project = tmp_path / "project"
    return M9P2Store(
        project / "agent" / "m9-p2" / "prospective.sqlite",
        project_root=project,
        preregistration_path=PREREGISTRATION,
    )


def _capture(index: int, *, stratum: QueryStratum) -> M9P2CaptureSubmission:
    return M9P2CaptureSubmission(
        source_event_id=f"task:m9:p2-case-{index}",
        question=f"Which unique prospective RAG method is evaluated in case {index}?",
        eligibility=Eligibility.GRAPH_ELIGIBLE,
        eligibility_reason=EligibilityReason.WITHIN_FROZEN_CORPUS_SCOPE,
        stratum=stratum,
    )


def _gold(record_id: str, gold_id: str, *, revision: int = 0) -> M9P2GoldSubmission:
    return M9P2GoldSubmission(
        record_id=record_id,
        expected_revision=revision,
        status=ReviewStatus.CONFIRMED,
        gold_candidate_ids=[gold_id],
        gold_in_frozen_corpus_ids=[gold_id],
        gold_basis=[
            M9GoldBasis(
                openalex_id=gold_id,
                kind=GoldBasisKind.OPENALEX_METADATA,
                authority_sha256="a" * 64,
            )
        ],
        notes="owner-authorized proxy review before condition reveal",
    )


def _ref(openalex_id: str, rank: int, *, graph_hint: bool = False) -> dict:
    document_id = f"doc-{openalex_id}"
    payload = {
        "rank": rank,
        "document_id": document_id,
        "document_name": f"openalex_{openalex_id}.txt",
        "openalex_id": openalex_id,
        "title": f"Prospective paper {openalex_id}",
        "score": 1.0,
        "matched_terms": ["rag"],
        "graph_terms": ["rag"] if graph_hint else [],
        "graph_paths": [],
    }
    if graph_hint:
        payload.update(
            {
                "reason_code": "query_relevance_plus_graph_path_specificity",
                "query_relevance_score": 0.5,
                "path_specificity_score": 0.5,
                "graph_paths": [
                    {
                        "kind": "query_entity",
                        "seed_document_id": None,
                        "source_entity": "RAG",
                        "relationship_id": None,
                        "relationship_description": None,
                        "target_entity": "RAG",
                        "target_document_id": document_id,
                        "matched_terms": ["rag"],
                        "score": 1.0,
                    }
                ],
            }
        )
    return payload


def _condition(
    *,
    record_id: str,
    question_sha256: str,
    receipt_sha256: str,
    b5_ids: list[str],
    b7_ids: list[str],
) -> M9P2ConditionSnapshot:
    b5_refs = [_ref(openalex_id, index) for index, openalex_id in enumerate(b5_ids, 1)]
    b7_refs = [_ref(openalex_id, index) for index, openalex_id in enumerate(b5_ids, 1)]
    b7_refs.extend(
        _ref(openalex_id, index, graph_hint=True)
        for index, openalex_id in enumerate(b7_ids[len(b5_ids) :], len(b5_ids) + 1)
    )
    payload = {
        "schema_version": "1.0",
        "purpose": "m9-p2-b5-b7-condition-snapshot",
        "record_id": record_id,
        "gold_freeze_sha256": receipt_sha256,
        "question_sha256": question_sha256,
        "corpus_id": "openalex-rag-abstracts-2020-2025-v1",
        "corpus_version": "formal-2026-08-26",
        "corpus_manifest_sha256": "b" * 64,
        "document_count": 198,
        "parquet_bundle_sha256": (
            "c837792525d387ff3257a3e7d0504a7ec73fc28e2f09399e96a339ef2a25617b"
        ),
        "b5_algorithm": {
            "id": "m8-b5-title-text-top3",
            "commit": "37e00cfdafbd21612de9e9c60807ed0aeac28783",
            "config_sha256": "c" * 64,
            "top_k": 3,
        },
        "b7_algorithm": {
            "id": "m9-b7-query-relevance-graph-path-specificity",
            "commit": "04f532704289e8188af1caae08fd0c1bfa2ae7e7",
            "manifest_sha256": ("e673f2d7f405f1fec2741c4e36490fc47f7b4d35cb838853519ab1fb2df5ae58"),
            "max_graph_hints": 1,
            "max_hops": 1,
        },
        "b5": {
            "variant": "B5",
            "status": "succeeded" if b5_refs else "no_match",
            "reason": "matched" if b5_refs else "no_catalog_match",
            "source_refs": b5_refs,
        },
        "b7": {
            "variant": "B7",
            "status": "succeeded" if b7_refs else "no_match",
            "reason": "matched" if b7_refs else "no_catalog_match",
            "source_refs": b7_refs,
        },
        "source_ref_validity_pass": True,
        "graph_path_validity_pass": True,
        "b5_seed_preserved_pass": True,
        "max_graph_hints_pass": True,
        "deterministic_replay_pass": True,
        "boundary_suite_sha256": (
            "153b21fd9c1b331ae9dda229d06ac0be81e76e0f91c0711273c5cbd24c74a0b6"
        ),
        "boundary_zero_candidates_pass": True,
        "parquet_read_only_pass": True,
        "usage": {
            "network_calls": 0,
            "model_calls": 0,
            "index_writes": 0,
            "paid_calls": 0,
            "reference_cost_cny": 0,
        },
    }
    payload["snapshot_sha256"] = _sha256_json(payload)
    return M9P2ConditionSnapshot.model_validate(payload)


def test_preregistration_excludes_p0_and_m8_and_is_hash_frozen(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        assert (
            hashlib.sha256(PREREGISTRATION.read_bytes()).hexdigest() == M9_P2_PREREGISTRATION_SHA256
        )
        excluded = M9P2CaptureSubmission(
            source_event_id="task:m9:p2-excluded-holdout",
            question=(
                "Which RAG paper describes AG-RAG as embedding autonomous AI agents "
                "into retrieval and generation for adaptive information processing?"
            ),
            eligibility=Eligibility.GRAPH_ELIGIBLE,
            eligibility_reason=EligibilityReason.WITHIN_FROZEN_CORPUS_SCOPE,
            stratum=QueryStratum.ALIAS_BRIDGE,
        )
        with pytest.raises(M9P2Error, match="excluded"):
            store.capture(excluded)
    finally:
        store.close()


def test_batch_blinding_and_passing_gate_a(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        first = _capture(0, stratum=QueryStratum.MULTI_CONCEPT)
        record_id, _, _ = store.capture(first)
        receipt = store.freeze_gold(_gold(record_id, "W1000"))
        with pytest.raises(M9P2Error, match="complete eligible batch"):
            store.attach_condition(
                _condition(
                    record_id=record_id,
                    question_sha256=m9_p2_question_sha256(first.question),
                    receipt_sha256=receipt.receipt_sha256,
                    b5_ids=["W1000"],
                    b7_ids=["W1000"],
                )
            )

        records = [
            (
                record_id,
                m9_p2_question_sha256(first.question),
                "W1000",
                receipt.receipt_sha256,
            )
        ]
        for index in range(1, 30):
            stratum = QueryStratum.RELATION_BRIDGE if index % 2 else QueryStratum.MULTI_CONCEPT
            submission = _capture(index, stratum=stratum)
            current_id, _, _ = store.capture(submission)
            current_gold = f"W{1000 + index}"
            current_receipt = store.freeze_gold(_gold(current_id, current_gold))
            records.append(
                (
                    current_id,
                    m9_p2_question_sha256(submission.question),
                    current_gold,
                    current_receipt.receipt_sha256,
                )
            )

        assert store.public_report().decision == M9P2Decision.READY_FOR_CONDITIONS
        for index, (current_id, question_hash, gold_id, receipt_hash) in enumerate(records):
            if index < 24:
                b5_ids = [gold_id]
                b7_ids = [gold_id]
            else:
                b5_ids = []
                b7_ids = [gold_id] if index < 27 else [f"W{9000 + index}"]
            store.attach_condition(
                _condition(
                    record_id=current_id,
                    question_sha256=question_hash,
                    receipt_sha256=receipt_hash,
                    b5_ids=b5_ids,
                    b7_ids=b7_ids,
                )
            )

        report = store.public_report()
        assert report.decision == M9P2Decision.GO_EVIDENCE_GATE
        assert report.counts.b5_miss_opportunities == 6
        assert report.counts.b7_recovered_opportunities == 3
        assert report.counts.recovered_strata == 2
        assert report.metrics.b7_recovery_rate == 0.5
        assert report.metrics.b5_candidate_recall == 0.8
        assert report.metrics.b7_candidate_recall == 0.9
        assert report.metrics.candidate_precision_delta == -0.1
        assert report.criteria.precision_drop_within_limit
        serialized = report.model_dump_json()
        assert "Which unique prospective" not in serialized
        assert "task:m9:" not in serialized
        assert "proxy review" not in serialized

        with pytest.raises(M9P2Error, match="Gold cannot change"):
            store.freeze_gold(_gold(record_id, "W1000", revision=1))
    finally:
        store.close()


def test_insufficient_initial_opportunities_requires_blind_extension(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        records = []
        for index in range(30):
            submission = _capture(index, stratum=QueryStratum.PAPER_NEIGHBOR)
            record_id, _, _ = store.capture(submission)
            gold_id = f"W{2000 + index}"
            receipt = store.freeze_gold(_gold(record_id, gold_id))
            records.append((record_id, submission, gold_id, receipt))
        for record_id, submission, gold_id, receipt in records:
            store.attach_condition(
                _condition(
                    record_id=record_id,
                    question_sha256=m9_p2_question_sha256(submission.question),
                    receipt_sha256=receipt.receipt_sha256,
                    b5_ids=[gold_id],
                    b7_ids=[gold_id],
                )
            )
        assert store.public_report().decision == M9P2Decision.COLLECT_MORE

        extension = _capture(30, stratum=QueryStratum.PAPER_NEIGHBOR)
        extension_id, _, _ = store.capture(extension)
        extension_receipt = store.freeze_gold(_gold(extension_id, "W2030"))
        with pytest.raises(M9P2Error, match="reach 50"):
            store.attach_condition(
                _condition(
                    record_id=extension_id,
                    question_sha256=m9_p2_question_sha256(extension.question),
                    receipt_sha256=extension_receipt.receipt_sha256,
                    b5_ids=["W2030"],
                    b7_ids=["W2030"],
                )
            )

        extension_records = [(extension_id, extension, "W2030", extension_receipt)]
        for index in range(31, 50):
            submission = _capture(index, stratum=QueryStratum.PAPER_NEIGHBOR)
            record_id, _, _ = store.capture(submission)
            gold_id = f"W{2000 + index}"
            receipt = store.freeze_gold(_gold(record_id, gold_id))
            extension_records.append((record_id, submission, gold_id, receipt))
        assert store.public_report().decision == M9P2Decision.READY_FOR_CONDITIONS
        for record_id, submission, gold_id, receipt in extension_records:
            store.attach_condition(
                _condition(
                    record_id=record_id,
                    question_sha256=m9_p2_question_sha256(submission.question),
                    receipt_sha256=receipt.receipt_sha256,
                    b5_ids=[gold_id],
                    b7_ids=[gold_id],
                )
            )
        assert store.public_report().decision == M9P2Decision.INCONCLUSIVE
    finally:
        store.close()
