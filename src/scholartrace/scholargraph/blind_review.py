"""Tamper-evident A/B blind review for private B3/B4 reports."""

from __future__ import annotations

import csv
import hashlib
import json
import random
import tempfile
from collections.abc import Mapping
from pathlib import Path

from scholartrace.scholargraph.evaluation import (
    B3B4ComparisonReport,
    B3B4EvaluationResult,
    B3B4Evaluator,
    EvaluationQuestion,
    QuestionSetHashes,
)
from scholartrace.scholargraph.experiment import (
    ExperimentError,
    PrivateEvaluationRow,
    PrivateExperimentArchive,
    require_private_agent_path,
)
from scholartrace.search.storage import write_json

IMMUTABLE_FIELDS = (
    "blind_id",
    "question_id",
    "subset",
    "question",
    "answer_a",
    "answer_b",
    "answer_a_sha256",
    "answer_b_sha256",
)
REVIEW_FIELDS = (*IMMUTABLE_FIELDS, "score_a", "score_b", "reviewer_notes")


def _stable_digest(payload: Mapping[str, str]) -> str:
    serialized = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _atomic_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8-sig",
        newline="",
        delete=False,
        dir=path.parent,
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
        temporary = Path(handle.name)
    temporary.replace(path)


def _private_index(
    rows: list[PrivateEvaluationRow],
    variant: str,
) -> dict[str, PrivateEvaluationRow]:
    indexed: dict[str, PrivateEvaluationRow] = {}
    for row in rows:
        if row.result.variant != variant:
            raise ExperimentError(f"private archive mixes rows in {variant}")
        if row.result.question_id in indexed:
            raise ExperimentError(f"duplicate private row: {row.result.question_id}/{variant}")
        indexed[row.result.question_id] = row
    return indexed


def prepare_blind_review(
    *,
    archive: PrivateExperimentArchive,
    questions: Mapping[str, EvaluationQuestion],
    review_path: Path,
    mapping_path: Path,
    seed: int,
) -> dict[str, object]:
    """Create a reviewer-facing CSV and a separate private label mapping."""

    review_destination = require_private_agent_path(review_path)
    mapping_destination = require_private_agent_path(mapping_path)
    if review_destination == mapping_destination:
        raise ExperimentError("blind review and mapping outputs must differ")
    b3 = _private_index(archive.b3, "B3")
    b4 = _private_index(archive.b4, "B4")
    if set(b3) != set(questions) or set(b4) != set(questions):
        raise ExperimentError("blind review input does not cover the frozen questions")

    rng = random.Random(seed)
    review_rows: list[dict[str, str]] = []
    mapping_entries: list[dict[str, str]] = []
    for index, question_id in enumerate(sorted(questions), 1):
        question = questions[question_id]
        pair = [b3[question_id], b4[question_id]]
        if rng.getrandbits(1):
            pair.reverse()
        blind_id = f"P{index:03d}"
        answer_a = pair[0].report or f"<NO ANSWER: status={pair[0].result.status}>"
        answer_b = pair[1].report or f"<NO ANSWER: status={pair[1].result.status}>"
        immutable = {
            "blind_id": blind_id,
            "question_id": question_id,
            "subset": question.subset,
            "question": question.question,
            "answer_a": answer_a,
            "answer_b": answer_b,
            "answer_a_sha256": hashlib.sha256(answer_a.encode("utf-8")).hexdigest(),
            "answer_b_sha256": hashlib.sha256(answer_b.encode("utf-8")).hexdigest(),
        }
        review_rows.append(
            {
                **immutable,
                "score_a": "",
                "score_b": "",
                "reviewer_notes": "",
            }
        )
        mapping_entries.append(
            {
                "blind_id": blind_id,
                "question_id": question_id,
                "label_a_variant": pair[0].result.variant,
                "label_b_variant": pair[1].result.variant,
                "row_sha256": _stable_digest(immutable),
                "b3_report_sha256": b3[question_id].result.report_sha256,
                "b4_report_sha256": b4[question_id].result.report_sha256,
            }
        )

    mapping = {
        "schema_version": "1.0",
        "purpose": "scholartrace-b3-b4-private-blind-mapping",
        "manifest_sha256": archive.manifest_sha256,
        "seed": seed,
        "row_count": len(review_rows),
        "entries": mapping_entries,
    }
    _atomic_csv(review_destination, review_rows)
    write_json(mapping_destination, mapping)
    return {
        "row_count": len(review_rows),
        "review_sha256": hashlib.sha256(review_destination.read_bytes()).hexdigest(),
        "score_scale": {"minimum": 0, "maximum": 4, "integer_only": True},
        "mapping_disclosed": False,
    }


def import_blind_scores(
    *,
    archive: PrivateExperimentArchive,
    questions: Mapping[str, EvaluationQuestion],
    review_path: Path,
    mapping_path: Path,
    question_set_hashes: QuestionSetHashes,
) -> B3B4ComparisonReport:
    """Verify immutable cells, reveal labels, and run the upstream paired evaluator."""

    require_private_agent_path(review_path)
    require_private_agent_path(mapping_path)
    if archive.manifest.question_sets != question_set_hashes:
        raise ExperimentError("question-set hashes drifted from the execution manifest")
    try:
        mapping_payload = json.loads(mapping_path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentError("cannot read the private blind mapping") from exc
    if (
        not isinstance(mapping_payload, dict)
        or mapping_payload.get("schema_version") != "1.0"
        or mapping_payload.get("purpose")
        != "scholartrace-b3-b4-private-blind-mapping"
        or mapping_payload.get("manifest_sha256") != archive.manifest_sha256
    ):
        raise ExperimentError("blind mapping does not match the private run")
    entries = mapping_payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ExperimentError("blind mapping is empty")
    mapping: dict[str, dict[str, object]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ExperimentError("blind mapping entry is invalid")
        blind_id = entry.get("blind_id")
        if not isinstance(blind_id, str) or blind_id in mapping:
            raise ExperimentError("blind mapping IDs are invalid or duplicated")
        mapping[blind_id] = entry
    if mapping_payload.get("row_count") != len(mapping):
        raise ExperimentError("blind mapping row count is inconsistent")

    try:
        with review_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != REVIEW_FIELDS:
                raise ExperimentError("blind review headers drifted")
            review_rows = list(reader)
    except OSError as exc:
        raise ExperimentError("cannot read the completed blind review") from exc
    if len(review_rows) != len(mapping):
        raise ExperimentError("blind review row coverage is incomplete")

    b3 = _private_index(archive.b3, "B3")
    b4 = _private_index(archive.b4, "B4")
    scored_b3: dict[str, B3B4EvaluationResult] = {}
    scored_b4: dict[str, B3B4EvaluationResult] = {}
    seen: set[str] = set()
    for row in review_rows:
        blind_id = row.get("blind_id", "")
        entry = mapping.get(blind_id)
        if entry is None or blind_id in seen:
            raise ExperimentError("blind review contains unknown or duplicate IDs")
        seen.add(blind_id)
        immutable = {field: row[field] for field in IMMUTABLE_FIELDS}
        if _stable_digest(immutable) != entry.get("row_sha256"):
            raise ExperimentError(f"{blind_id}: immutable blind review content changed")
        question_id = entry.get("question_id")
        if not isinstance(question_id, str) or question_id not in questions:
            raise ExperimentError(f"{blind_id}: mapping question is invalid")
        if (
            entry.get("b3_report_sha256") != b3[question_id].result.report_sha256
            or entry.get("b4_report_sha256") != b4[question_id].result.report_sha256
        ):
            raise ExperimentError(f"{blind_id}: source report hashes drifted")
        score_a = _parse_score(row.get("score_a", ""), f"{blind_id} score_a")
        score_b = _parse_score(row.get("score_b", ""), f"{blind_id} score_b")
        variants = {
            entry.get("label_a_variant"): score_a,
            entry.get("label_b_variant"): score_b,
        }
        if set(variants) != {"B3", "B4"}:
            raise ExperimentError(f"{blind_id}: mapping labels are invalid")
        scored_b3[question_id] = b3[question_id].result.model_copy(
            update={"quality_score": variants["B3"]}
        )
        scored_b4[question_id] = b4[question_id].result.model_copy(
            update={"quality_score": variants["B4"]}
        )
    if seen != set(mapping):
        raise ExperimentError("blind mapping contains IDs absent from the review")
    return B3B4Evaluator().compare(
        questions=questions,
        b3_results=[scored_b3[item] for item in sorted(scored_b3)],
        b4_results=[scored_b4[item] for item in sorted(scored_b4)],
        question_set_hashes=question_set_hashes,
    )


def _parse_score(value: str, label: str) -> int:
    text = value.strip()
    if not text:
        raise ExperimentError(f"{label} is required")
    try:
        score = int(text)
    except ValueError as exc:
        raise ExperimentError(f"{label} must be an integer") from exc
    if score < 0 or score > 4:
        raise ExperimentError(f"{label} must be between 0 and 4")
    return score
