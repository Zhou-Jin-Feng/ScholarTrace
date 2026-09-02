"""Private M9-P0 real-miss observations and sanitized gate reporting."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from scholartrace.contracts import Sha256, StableId
from scholartrace.scholargraph.models import CORPUS_ID, CORPUS_MANIFEST_SHA256
from scholartrace.search.identifiers import normalize_openalex_id
from scholartrace.search.storage import write_model

M9_B5_ALGORITHM_ID = "m8-b5-title-text-top3"
M9_B5_ALGORITHM_COMMIT = "37e00cfdafbd21612de9e9c60807ed0aeac28783"
M9_CORPUS_VERSION = "formal-2026-08-26"
M9_INITIAL_ELIGIBLE_TARGET = 30
M9_MAX_ELIGIBLE_TARGET = 50
M9_MIN_CONFIRMED_MISSES = 8
M9_MIN_CORPUS_OPPORTUNITIES = 6
M9_MIN_GRAPH_FIXABLE_MISSES = 4
M9_MIN_GRAPH_ROOT_CAUSES = 2
M9_PARQUET_BUNDLE_SHA256 = (
    "c837792525d387ff3257a3e7d0504a7ec73fc28e2f09399e96a339ef2a25617b"
)

PrivateText = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=4000),
]
OpenAlexId = Annotated[str, Field(pattern=r"^W[0-9]+$")]


class M9ObservationError(ValueError):
    """An M9 observation is unsafe, inconsistent, or not reproducible."""


class M9Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SampleOrigin(StrEnum):
    REAL = "real"
    FIXTURE = "fixture"


class Eligibility(StrEnum):
    GRAPH_ELIGIBLE = "graph_eligible"
    INELIGIBLE = "ineligible"


class EligibilityReason(StrEnum):
    WITHIN_FROZEN_CORPUS_SCOPE = "within_frozen_corpus_scope"
    OUTSIDE_TOPIC = "outside_topic"
    OUTSIDE_YEAR_RANGE = "outside_year_range"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    FULLTEXT_REQUIRED = "fulltext_required"
    METADATA_ONLY = "metadata_only"
    AMBIGUOUS = "ambiguous"


class QueryStratum(StrEnum):
    ALIAS_BRIDGE = "alias_bridge"
    RELATION_BRIDGE = "relation_bridge"
    PAPER_NEIGHBOR = "paper_neighbor"
    MULTI_CONCEPT = "multi_concept"
    OTHER = "other"


class ReviewStatus(StrEnum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"


class RootCause(StrEnum):
    G1_ALIAS = "G1_ALIAS"
    G2_RELATION = "G2_RELATION"
    G3_RANKING = "G3_RANKING"
    G4_GRAPH_DATA = "G4_GRAPH_DATA"
    N1_CORPUS_GAP = "N1_CORPUS_GAP"
    N2_FULLTEXT_ONLY = "N2_FULLTEXT_ONLY"
    N3_SOURCE_MISMATCH = "N3_SOURCE_MISMATCH"
    N4_BOUNDARY_AMBIGUOUS = "N4_BOUNDARY_AMBIGUOUS"


GRAPH_ROOT_CAUSES = frozenset(
    {
        RootCause.G1_ALIAS,
        RootCause.G2_RELATION,
        RootCause.G3_RANKING,
        RootCause.G4_GRAPH_DATA,
    }
)


class GraphPathStatus(StrEnum):
    PATH_FOUND = "path_found"
    PATH_MISSING = "path_missing"
    PATH_INVALID = "path_invalid"
    NOT_APPLICABLE = "not_applicable"


class GoldBasisKind(StrEnum):
    OPENALEX_METADATA = "openalex_metadata"
    CITATION_PATH = "citation_path"
    PUBLIC_PAPER_IDENTITY = "public_paper_identity"
    VERIFIED_EVIDENCE = "verified_evidence"


class P0Decision(StrEnum):
    COLLECT_MORE = "COLLECT_MORE"
    GO_IMPLEMENT = "GO_IMPLEMENT"
    NO_GO_INSUFFICIENT_NEED = "NO_GO_INSUFFICIENT_NEED"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_m9_question(question: str) -> str:
    normalized = unicodedata.normalize("NFKC", question).casefold()
    return " ".join(normalized.split())


def m9_question_sha256(question: str) -> str:
    return _sha256_text(normalize_m9_question(question))


def m9_b5_config_sha256() -> str:
    return _sha256_text(
        _canonical_json(
            {
                "algorithm_commit": M9_B5_ALGORITHM_COMMIT,
                "algorithm_id": M9_B5_ALGORITHM_ID,
                "corpus_id": CORPUS_ID,
                "corpus_manifest_sha256": CORPUS_MANIFEST_SHA256,
                "corpus_version": M9_CORPUS_VERSION,
                "top_k": 3,
            }
        )
    )


class M9B5Snapshot(M9Model):
    schema_version: Literal["1.0"] = "1.0"
    algorithm_id: Literal["m8-b5-title-text-top3"] = "m8-b5-title-text-top3"
    algorithm_commit: Literal["37e00cfdafbd21612de9e9c60807ed0aeac28783"] = (
        "37e00cfdafbd21612de9e9c60807ed0aeac28783"
    )
    corpus_id: Literal["openalex-rag-abstracts-2020-2025-v1"] = (
        "openalex-rag-abstracts-2020-2025-v1"
    )
    corpus_version: Literal["formal-2026-08-26"] = "formal-2026-08-26"
    corpus_manifest_sha256: Literal[
        "168671c6f9f68ed2c8017e8d3d14396a455559a6a55628a7b0c40d32a80ea36c"
    ] = "168671c6f9f68ed2c8017e8d3d14396a455559a6a55628a7b0c40d32a80ea36c"
    document_count: Literal[198] = 198
    top_k: Literal[3] = 3
    question_sha256: Sha256
    status: Literal["succeeded", "no_match"]
    reason: Literal["matched", "no_catalog_match"]
    candidate_openalex_ids: list[OpenAlexId] = Field(max_length=3)
    parquet_bundle_sha256: Literal[
        "c837792525d387ff3257a3e7d0504a7ec73fc28e2f09399e96a339ef2a25617b"
    ]
    parquet_read_only_pass: Literal[True] = True
    config_sha256: Sha256
    snapshot_sha256: Sha256

    @model_validator(mode="after")
    def validate_snapshot(self) -> M9B5Snapshot:
        normalized = [normalize_openalex_id(item) for item in self.candidate_openalex_ids]
        if any(item is None for item in normalized):
            raise ValueError("B5 snapshot contains an invalid OpenAlex ID")
        if len(self.candidate_openalex_ids) != len(set(self.candidate_openalex_ids)):
            raise ValueError("B5 snapshot contains duplicate candidates")
        if self.status == "succeeded" and not self.candidate_openalex_ids:
            raise ValueError("successful B5 snapshot requires candidates")
        if self.status == "no_match" and self.candidate_openalex_ids:
            raise ValueError("no-match B5 snapshot cannot contain candidates")
        if (self.status, self.reason) not in {
            ("succeeded", "matched"),
            ("no_match", "no_catalog_match"),
        }:
            raise ValueError("B5 snapshot status and reason are inconsistent")
        if self.config_sha256 != m9_b5_config_sha256():
            raise ValueError("B5 configuration hash differs from the frozen M9 baseline")
        expected_snapshot = _sha256_text(
            _canonical_json(self.model_dump(mode="json", exclude={"snapshot_sha256"}))
        )
        if self.snapshot_sha256 != expected_snapshot:
            raise ValueError("B5 snapshot hash is invalid")
        return self


def build_m9_b5_snapshot(
    *,
    question: str,
    status: Literal["succeeded", "no_match"],
    reason: str,
    candidate_openalex_ids: list[str],
    parquet_bundle_sha256: str,
) -> M9B5Snapshot:
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "algorithm_id": M9_B5_ALGORITHM_ID,
        "algorithm_commit": M9_B5_ALGORITHM_COMMIT,
        "corpus_id": CORPUS_ID,
        "corpus_version": M9_CORPUS_VERSION,
        "corpus_manifest_sha256": CORPUS_MANIFEST_SHA256,
        "document_count": 198,
        "top_k": 3,
        "question_sha256": m9_question_sha256(question),
        "status": status,
        "reason": reason,
        "candidate_openalex_ids": candidate_openalex_ids,
        "parquet_bundle_sha256": parquet_bundle_sha256,
        "parquet_read_only_pass": True,
        "config_sha256": m9_b5_config_sha256(),
    }
    payload["snapshot_sha256"] = _sha256_text(_canonical_json(payload))
    return M9B5Snapshot.model_validate(payload)


class M9CaptureSubmission(M9Model):
    schema_version: Literal["1.0"] = "1.0"
    source_event_id: StableId
    sample_origin: SampleOrigin = SampleOrigin.REAL
    question: Annotated[
        str,
        StringConstraints(strict=True, strip_whitespace=True, min_length=3, max_length=2000),
    ]
    eligibility: Eligibility
    eligibility_reason: EligibilityReason
    stratum: QueryStratum | None = None
    b5_snapshot: M9B5Snapshot | None = None

    @model_validator(mode="after")
    def validate_eligibility(self) -> M9CaptureSubmission:
        if self.eligibility == Eligibility.GRAPH_ELIGIBLE:
            if self.eligibility_reason != EligibilityReason.WITHIN_FROZEN_CORPUS_SCOPE:
                raise ValueError("graph-eligible capture has an incompatible reason")
            if self.stratum is None or self.b5_snapshot is None:
                raise ValueError("graph-eligible capture requires a stratum and B5 snapshot")
            if self.b5_snapshot.question_sha256 != m9_question_sha256(self.question):
                raise ValueError("B5 snapshot does not belong to the captured question")
        elif (
            self.eligibility_reason == EligibilityReason.WITHIN_FROZEN_CORPUS_SCOPE
            or self.stratum is not None
            or self.b5_snapshot is not None
        ):
            raise ValueError("ineligible capture must not carry graph execution fields")
        return self


class M9GoldBasis(M9Model):
    openalex_id: OpenAlexId
    kind: GoldBasisKind
    authority_sha256: Sha256


class M9ReviewSubmission(M9Model):
    schema_version: Literal["1.0"] = "1.0"
    record_id: Annotated[str, Field(pattern=r"^m9-p0-[0-9]{6}$")]
    expected_revision: int = Field(ge=0)
    status: ReviewStatus
    gold_candidate_ids: list[OpenAlexId] = Field(default_factory=list, max_length=50)
    gold_in_frozen_corpus_ids: list[OpenAlexId] = Field(
        default_factory=list, max_length=50
    )
    gold_basis: list[M9GoldBasis] = Field(default_factory=list, max_length=50)
    graph_path_status: GraphPathStatus = GraphPathStatus.NOT_APPLICABLE
    root_cause: RootCause | None = None
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def validate_review_shape(self) -> M9ReviewSubmission:
        for label, values in (
            ("gold", self.gold_candidate_ids),
            ("frozen-corpus gold", self.gold_in_frozen_corpus_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} IDs contain duplicates")
        if not set(self.gold_in_frozen_corpus_ids).issubset(self.gold_candidate_ids):
            raise ValueError("frozen-corpus gold IDs must be a subset of all gold IDs")
        basis_ids = [item.openalex_id for item in self.gold_basis]
        if len(basis_ids) != len(set(basis_ids)):
            raise ValueError("each gold ID must have exactly one authority basis")
        if self.status == ReviewStatus.CONFIRMED:
            if not self.gold_candidate_ids or set(basis_ids) != set(self.gold_candidate_ids):
                raise ValueError("confirmed review requires one authority basis per gold ID")
        elif self.status == ReviewStatus.REJECTED and any(
            (
                self.gold_candidate_ids,
                self.gold_in_frozen_corpus_ids,
                self.gold_basis,
                self.root_cause,
                self.notes,
            )
        ):
            raise ValueError("rejected review cannot carry gold, root cause, or notes")
        return self


class M9StoredObservation(M9Model):
    record_id: str
    sequence_id: int
    source_event_sha256: Sha256
    sample_origin: SampleOrigin
    question: str
    question_sha256: Sha256
    eligibility: Eligibility
    eligibility_reason: EligibilityReason
    stratum: QueryStratum | None
    b5_snapshot: M9B5Snapshot | None
    capture_sha256: Sha256
    previous_record_sha256: Sha256
    record_sha256: Sha256
    captured_at: datetime


class M9StoredReview(M9Model):
    submission: M9ReviewSubmission
    revision: int
    review_sha256: Sha256
    previous_review_sha256: Sha256
    review_entry_sha256: Sha256
    reviewed_at: datetime


class M9P0PublicRecord(M9Model):
    record_id: str
    sequence_id: int
    sample_origin: Literal["real"] = "real"
    question_sha256: Sha256
    eligibility: Eligibility
    eligibility_reason: EligibilityReason
    stratum: QueryStratum | None
    b5_snapshot: M9B5Snapshot | None
    review_status: Literal["pending", "confirmed", "rejected", "ambiguous"]
    review_revision: int = Field(ge=0)
    review_entry_sha256: Sha256 | None
    gold_candidate_ids: list[OpenAlexId]
    gold_in_frozen_corpus_ids: list[OpenAlexId]
    gold_basis_kinds: list[GoldBasisKind]
    b5_missed_ids: list[OpenAlexId]
    graph_path_status: GraphPathStatus
    root_cause: RootCause | None


class M9P0Counts(M9Model):
    real_observations: int = Field(ge=0)
    fixture_observations_excluded: int = Field(ge=0)
    eligible_real_observations: int = Field(ge=0, le=M9_MAX_ELIGIBLE_TARGET)
    reviewed_eligible_observations: int = Field(ge=0)
    pending_eligible_reviews: int = Field(ge=0)
    confirmed_b5_misses: int = Field(ge=0)
    frozen_corpus_miss_opportunities: int = Field(ge=0)
    graph_fixable_misses: int = Field(ge=0)
    distinct_graph_root_causes: int = Field(ge=0, le=4)


class M9P0Criteria(M9Model):
    initial_window_complete: bool
    all_eligible_reviewed: bool
    minimum_confirmed_misses: bool
    minimum_corpus_opportunities: bool
    minimum_graph_fixable_misses: bool
    minimum_graph_root_causes: bool
    capture_chain_valid: bool
    all_included_records_reproducible: bool
    fixtures_excluded_from_gate: Literal[True] = True


class M9P0Usage(M9Model):
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    documind_calls: Literal[0] = 0
    paid_calls: Literal[0] = 0
    reference_cost_cny: Literal[0] = 0


class M9P0PublicReport(M9Model):
    schema_version: Literal["1.0"] = "1.0"
    purpose: Literal["m9-p0-real-miss-observation-gate"] = (
        "m9-p0-real-miss-observation-gate"
    )
    protocol: Literal["docs/M9_REAL_MISS_PROTOCOL.md"] = (
        "docs/M9_REAL_MISS_PROTOCOL.md"
    )
    decision: P0Decision
    scholargraph_default_enabled: Literal[False] = False
    public_payload_contains_raw_questions: Literal[False] = False
    counts: M9P0Counts
    criteria: M9P0Criteria
    root_cause_counts: dict[str, int]
    record_chain_head_sha256: Sha256
    records: list[M9P0PublicRecord]
    blockers: list[str]
    usage: M9P0Usage = Field(default_factory=M9P0Usage)
    limitations: list[str]


def require_project_agent_path(path: Path, *, project_root: Path) -> Path:
    resolved = path.resolve()
    agent_root = (project_root.resolve() / "agent").resolve()
    try:
        resolved.relative_to(agent_root)
    except ValueError as exc:
        raise M9ObservationError("private M9 data must stay below the project agent/") from exc
    return resolved


class M9P0Store:
    """Append-only observation/review ledger stored below the private agent directory."""

    def __init__(self, path: Path, *, project_root: Path) -> None:
        self.path = require_project_agent_path(path, project_root=project_root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS observations (
                sequence_id INTEGER PRIMARY KEY,
                record_id TEXT NOT NULL UNIQUE,
                source_event_sha256 TEXT NOT NULL UNIQUE,
                sample_origin TEXT NOT NULL,
                question TEXT NOT NULL,
                question_sha256 TEXT NOT NULL UNIQUE,
                eligibility TEXT NOT NULL,
                eligibility_reason TEXT NOT NULL,
                stratum TEXT,
                b5_snapshot_json TEXT,
                capture_sha256 TEXT NOT NULL,
                previous_record_sha256 TEXT NOT NULL,
                record_sha256 TEXT NOT NULL UNIQUE,
                captured_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reviews (
                record_id TEXT NOT NULL REFERENCES observations(record_id),
                revision INTEGER NOT NULL,
                submission_json TEXT NOT NULL,
                review_sha256 TEXT NOT NULL,
                previous_review_sha256 TEXT NOT NULL,
                review_entry_sha256 TEXT NOT NULL UNIQUE,
                reviewed_at TEXT NOT NULL,
                PRIMARY KEY(record_id, revision)
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def capture(self, submission: M9CaptureSubmission) -> M9StoredObservation:
        source_event_sha256 = _sha256_text(submission.source_event_id)
        question_sha256 = m9_question_sha256(submission.question)
        capture_payload = {
            "source_event_sha256": source_event_sha256,
            "sample_origin": submission.sample_origin.value,
            "question": submission.question,
            "question_sha256": question_sha256,
            "eligibility": submission.eligibility.value,
            "eligibility_reason": submission.eligibility_reason.value,
            "stratum": submission.stratum.value if submission.stratum else None,
            "b5_snapshot": (
                submission.b5_snapshot.model_dump(mode="json")
                if submission.b5_snapshot
                else None
            ),
        }
        capture_sha256 = _sha256_text(_canonical_json(capture_payload))
        existing = self.connection.execute(
            "SELECT * FROM observations WHERE source_event_sha256 = ?",
            (source_event_sha256,),
        ).fetchone()
        if existing is not None:
            if str(existing["capture_sha256"]) != capture_sha256:
                raise M9ObservationError("source event was reused with different content")
            return self._observation_from_row(existing)
        duplicate = self.connection.execute(
            "SELECT record_id FROM observations WHERE question_sha256 = ?",
            (question_sha256,),
        ).fetchone()
        if duplicate is not None:
            raise M9ObservationError(
                f"duplicate normalized question already captured as {duplicate['record_id']}"
            )
        if (
            submission.sample_origin == SampleOrigin.REAL
            and submission.eligibility == Eligibility.GRAPH_ELIGIBLE
            and self._eligible_real_count() >= M9_MAX_ELIGIBLE_TARGET
        ):
            raise M9ObservationError("M9-P0 reached the frozen 50-question eligible limit")

        last = self.connection.execute(
            "SELECT sequence_id, record_sha256 FROM observations ORDER BY sequence_id DESC LIMIT 1"
        ).fetchone()
        sequence_id = int(last["sequence_id"]) + 1 if last is not None else 1
        previous_sha256 = str(last["record_sha256"]) if last is not None else "0" * 64
        record_id = f"m9-p0-{sequence_id:06d}"
        record_sha256 = _sha256_text(
            _canonical_json(
                {
                    "capture_sha256": capture_sha256,
                    "previous_record_sha256": previous_sha256,
                    "record_id": record_id,
                    "sequence_id": sequence_id,
                }
            )
        )
        captured_at = datetime.now(UTC).isoformat()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO observations(
                    sequence_id, record_id, source_event_sha256, sample_origin,
                    question, question_sha256, eligibility, eligibility_reason,
                    stratum, b5_snapshot_json, capture_sha256,
                    previous_record_sha256, record_sha256, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sequence_id,
                    record_id,
                    source_event_sha256,
                    submission.sample_origin.value,
                    submission.question,
                    question_sha256,
                    submission.eligibility.value,
                    submission.eligibility_reason.value,
                    submission.stratum.value if submission.stratum else None,
                    (
                        _canonical_json(submission.b5_snapshot.model_dump(mode="json"))
                        if submission.b5_snapshot
                        else None
                    ),
                    capture_sha256,
                    previous_sha256,
                    record_sha256,
                    captured_at,
                ),
            )
        return self.get_observation(record_id)

    def review(self, submission: M9ReviewSubmission) -> M9StoredReview:
        observation = self.get_observation(submission.record_id)
        self._validate_review(observation, submission)
        current = self.latest_review(submission.record_id)
        current_revision = current.revision if current else 0
        if submission.expected_revision != current_revision:
            raise M9ObservationError(
                f"review revision conflict: expected {submission.expected_revision}, "
                f"current {current_revision}"
            )
        revision = current_revision + 1
        previous_sha256 = current.review_entry_sha256 if current else "0" * 64
        serialized = _canonical_json(submission.model_dump(mode="json"))
        review_sha256 = _sha256_text(serialized)
        entry_sha256 = _sha256_text(
            _canonical_json(
                {
                    "previous_review_sha256": previous_sha256,
                    "record_id": submission.record_id,
                    "review_sha256": review_sha256,
                    "revision": revision,
                }
            )
        )
        reviewed_at = datetime.now(UTC).isoformat()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO reviews(
                    record_id, revision, submission_json, review_sha256,
                    previous_review_sha256, review_entry_sha256, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission.record_id,
                    revision,
                    serialized,
                    review_sha256,
                    previous_sha256,
                    entry_sha256,
                    reviewed_at,
                ),
            )
        stored = self.latest_review(submission.record_id)
        if stored is None:
            raise M9ObservationError("review insert did not persist")
        return stored

    def get_observation(self, record_id: str) -> M9StoredObservation:
        row = self.connection.execute(
            "SELECT * FROM observations WHERE record_id = ?", (record_id,)
        ).fetchone()
        if row is None:
            raise M9ObservationError(f"unknown M9 record: {record_id}")
        return self._observation_from_row(row)

    def observations(self) -> list[M9StoredObservation]:
        rows = self.connection.execute(
            "SELECT * FROM observations ORDER BY sequence_id"
        ).fetchall()
        return [self._observation_from_row(row) for row in rows]

    def latest_review(self, record_id: str) -> M9StoredReview | None:
        row = self.connection.execute(
            "SELECT * FROM reviews WHERE record_id = ? ORDER BY revision DESC LIMIT 1",
            (record_id,),
        ).fetchone()
        return self._review_from_row(row) if row is not None else None

    def verify_integrity(self) -> bool:
        previous = "0" * 64
        for expected_sequence, row in enumerate(self.observations(), start=1):
            if row.sequence_id != expected_sequence:
                return False
            if row.record_id != f"m9-p0-{expected_sequence:06d}":
                return False
            if row.previous_record_sha256 != previous:
                return False
            capture_payload = {
                "source_event_sha256": row.source_event_sha256,
                "sample_origin": row.sample_origin.value,
                "question": row.question,
                "question_sha256": row.question_sha256,
                "eligibility": row.eligibility.value,
                "eligibility_reason": row.eligibility_reason.value,
                "stratum": row.stratum.value if row.stratum else None,
                "b5_snapshot": (
                    row.b5_snapshot.model_dump(mode="json") if row.b5_snapshot else None
                ),
            }
            if row.capture_sha256 != _sha256_text(_canonical_json(capture_payload)):
                return False
            expected_record = _sha256_text(
                _canonical_json(
                    {
                        "capture_sha256": row.capture_sha256,
                        "previous_record_sha256": previous,
                        "record_id": row.record_id,
                        "sequence_id": row.sequence_id,
                    }
                )
            )
            if row.record_sha256 != expected_record:
                return False
            previous = row.record_sha256
            review_rows = self.connection.execute(
                "SELECT * FROM reviews WHERE record_id = ? ORDER BY revision",
                (row.record_id,),
            ).fetchall()
            previous_review = "0" * 64
            for revision, review_row in enumerate(review_rows, start=1):
                review = self._review_from_row(review_row)
                if review.revision != revision or review.previous_review_sha256 != previous_review:
                    return False
                serialized = _canonical_json(review.submission.model_dump(mode="json"))
                if review.review_sha256 != _sha256_text(serialized):
                    return False
                expected_entry = _sha256_text(
                    _canonical_json(
                        {
                            "previous_review_sha256": previous_review,
                            "record_id": row.record_id,
                            "review_sha256": review.review_sha256,
                            "revision": revision,
                        }
                    )
                )
                if review.review_entry_sha256 != expected_entry:
                    return False
                previous_review = review.review_entry_sha256
        return True

    def public_report(self) -> M9P0PublicReport:
        observations = self.observations()
        integrity_pass = self.verify_integrity()
        real = [item for item in observations if item.sample_origin == SampleOrigin.REAL]
        eligible = [item for item in real if item.eligibility == Eligibility.GRAPH_ELIGIBLE]
        public_records: list[M9P0PublicRecord] = []
        confirmed_misses = 0
        corpus_opportunities = 0
        graph_fixable_misses = 0
        root_causes: Counter[str] = Counter()
        graph_causes: Counter[str] = Counter()
        reviewed_eligible = 0

        for observation in real:
            review = self.latest_review(observation.record_id)
            public = self._public_record(observation, review)
            public_records.append(public)
            if observation.eligibility != Eligibility.GRAPH_ELIGIBLE or review is None:
                continue
            reviewed_eligible += 1
            if review.submission.status != ReviewStatus.CONFIRMED:
                continue
            missed = set(public.b5_missed_ids)
            if not missed:
                continue
            confirmed_misses += 1
            if public.root_cause is not None:
                root_causes[str(public.root_cause)] += 1
            frozen_missed = missed & set(public.gold_in_frozen_corpus_ids)
            if frozen_missed:
                corpus_opportunities += 1
            if frozen_missed and public.root_cause in GRAPH_ROOT_CAUSES:
                graph_fixable_misses += 1
                graph_causes[str(public.root_cause)] += 1

        pending_reviews = len(eligible) - reviewed_eligible
        reproducible = all(
            item.b5_snapshot is not None
            and item.b5_snapshot.question_sha256 == item.question_sha256
            for item in eligible
        )
        counts = M9P0Counts(
            real_observations=len(real),
            fixture_observations_excluded=len(observations) - len(real),
            eligible_real_observations=len(eligible),
            reviewed_eligible_observations=reviewed_eligible,
            pending_eligible_reviews=pending_reviews,
            confirmed_b5_misses=confirmed_misses,
            frozen_corpus_miss_opportunities=corpus_opportunities,
            graph_fixable_misses=graph_fixable_misses,
            distinct_graph_root_causes=len(graph_causes),
        )
        criteria = M9P0Criteria(
            initial_window_complete=len(eligible) >= M9_INITIAL_ELIGIBLE_TARGET,
            all_eligible_reviewed=bool(eligible) and pending_reviews == 0,
            minimum_confirmed_misses=confirmed_misses >= M9_MIN_CONFIRMED_MISSES,
            minimum_corpus_opportunities=corpus_opportunities
            >= M9_MIN_CORPUS_OPPORTUNITIES,
            minimum_graph_fixable_misses=graph_fixable_misses
            >= M9_MIN_GRAPH_FIXABLE_MISSES,
            minimum_graph_root_causes=len(graph_causes) >= M9_MIN_GRAPH_ROOT_CAUSES,
            capture_chain_valid=integrity_pass,
            all_included_records_reproducible=reproducible,
        )
        gate_ready = all(criteria.model_dump(mode="python").values())
        if gate_ready:
            decision = P0Decision.GO_IMPLEMENT
        elif (
            len(eligible) >= M9_MAX_ELIGIBLE_TARGET
            and pending_reviews == 0
            and integrity_pass
            and reproducible
        ):
            decision = P0Decision.NO_GO_INSUFFICIENT_NEED
        else:
            decision = P0Decision.COLLECT_MORE
        blockers = self._blockers(criteria, counts, decision)
        chain_head = observations[-1].record_sha256 if observations else "0" * 64
        return M9P0PublicReport(
            decision=decision,
            counts=counts,
            criteria=criteria,
            root_cause_counts=dict(sorted(root_causes.items())),
            record_chain_head_sha256=chain_head,
            records=public_records,
            blockers=blockers,
            limitations=[
                "Only consecutive records marked real can affect the P0 gate.",
                (
                    "Question text, source event identity, reviewer notes, and review "
                    "history stay private."
                ),
                "P0 GO permits a treatment proposal only; it does not enable ScholarGraph.",
                "Fixture validation cannot establish real product need or retrieval gain.",
            ],
        )

    def write_public_report(self, path: Path) -> str:
        return write_model(path, self.public_report())

    def _eligible_real_count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM observations "
            "WHERE sample_origin = ? AND eligibility = ?",
            (SampleOrigin.REAL.value, Eligibility.GRAPH_ELIGIBLE.value),
        ).fetchone()
        return int(row["count"])

    @staticmethod
    def _validate_review(
        observation: M9StoredObservation, submission: M9ReviewSubmission
    ) -> None:
        if observation.eligibility != Eligibility.GRAPH_ELIGIBLE:
            if submission.status != ReviewStatus.REJECTED:
                raise M9ObservationError("ineligible observations can only be rejected")
            return
        if submission.status != ReviewStatus.CONFIRMED:
            return
        if observation.b5_snapshot is None:
            raise M9ObservationError("eligible observation has no B5 snapshot")
        missed = set(submission.gold_candidate_ids) - set(
            observation.b5_snapshot.candidate_openalex_ids
        )
        frozen_missed = missed & set(submission.gold_in_frozen_corpus_ids)
        if not missed:
            if submission.root_cause is not None:
                raise M9ObservationError("B5-covered review cannot assign a miss root cause")
            if submission.graph_path_status != GraphPathStatus.NOT_APPLICABLE:
                raise M9ObservationError("B5-covered review cannot assign graph path status")
            return
        if submission.root_cause is None:
            raise M9ObservationError("confirmed B5 miss requires a root cause")
        if submission.root_cause in GRAPH_ROOT_CAUSES and not frozen_missed:
            raise M9ObservationError("graph root cause requires a missed gold in the frozen corpus")
        if submission.root_cause == RootCause.N1_CORPUS_GAP and frozen_missed:
            raise M9ObservationError("corpus-gap root cause conflicts with frozen-corpus gold")
        if submission.root_cause == RootCause.G2_RELATION and (
            submission.graph_path_status != GraphPathStatus.PATH_MISSING
        ):
            raise M9ObservationError("G2_RELATION requires path_missing")
        if submission.root_cause == RootCause.G3_RANKING and (
            submission.graph_path_status != GraphPathStatus.PATH_FOUND
        ):
            raise M9ObservationError("G3_RANKING requires path_found")
        if submission.root_cause == RootCause.G4_GRAPH_DATA and (
            submission.graph_path_status != GraphPathStatus.PATH_INVALID
        ):
            raise M9ObservationError("G4_GRAPH_DATA requires path_invalid")
        if submission.root_cause == RootCause.N4_BOUNDARY_AMBIGUOUS:
            raise M9ObservationError("ambiguous boundary cannot be a confirmed miss")

    @staticmethod
    def _blockers(
        criteria: M9P0Criteria, counts: M9P0Counts, decision: P0Decision
    ) -> list[str]:
        if decision == P0Decision.GO_IMPLEMENT:
            return ["Project-owner approval is required before proposing one treatment."]
        blockers: list[str] = []
        if not criteria.initial_window_complete:
            blockers.append(
                f"Collect {M9_INITIAL_ELIGIBLE_TARGET - counts.eligible_real_observations} "
                "more consecutive graph-eligible real subquestions for the initial window."
            )
        elif (
            decision == P0Decision.COLLECT_MORE
            and counts.eligible_real_observations < M9_MAX_ELIGIBLE_TARGET
        ):
            blockers.append(
                f"Continue consecutive collection with up to "
                f"{M9_MAX_ELIGIBLE_TARGET - counts.eligible_real_observations} "
                "additional eligible subquestions."
            )
        if counts.pending_eligible_reviews:
            blockers.append(
                f"Complete human review for {counts.pending_eligible_reviews} eligible records."
            )
        if not criteria.minimum_confirmed_misses:
            blockers.append("Fewer than 8 confirmed B5 misses have been observed.")
        if not criteria.minimum_corpus_opportunities:
            blockers.append("Fewer than 6 frozen-corpus B5 miss opportunities exist.")
        if not criteria.minimum_graph_fixable_misses:
            blockers.append("Fewer than 4 misses have a graph-fixable primary cause.")
        if not criteria.minimum_graph_root_causes:
            blockers.append("Graph-fixable misses cover fewer than two root-cause classes.")
        if not criteria.capture_chain_valid:
            blockers.append("Observation or review hash-chain integrity failed.")
        if not criteria.all_included_records_reproducible:
            blockers.append("At least one eligible record lacks a reproducible frozen B5 snapshot.")
        if decision == P0Decision.NO_GO_INSUFFICIENT_NEED:
            blockers.append(
                "The frozen 50-question eligible window is exhausted; do not modify B5."
            )
        return blockers

    @staticmethod
    def _public_record(
        observation: M9StoredObservation, review: M9StoredReview | None
    ) -> M9P0PublicRecord:
        if review is None:
            status: Literal["pending", "confirmed", "rejected", "ambiguous"] = "pending"
            revision = 0
            review_entry_sha256 = None
            gold_ids: list[str] = []
            frozen_ids: list[str] = []
            basis_kinds: list[GoldBasisKind] = []
            graph_path_status = GraphPathStatus.NOT_APPLICABLE
            root_cause = None
        else:
            status = review.submission.status.value
            revision = review.revision
            review_entry_sha256 = review.review_entry_sha256
            gold_ids = sorted(review.submission.gold_candidate_ids)
            frozen_ids = sorted(review.submission.gold_in_frozen_corpus_ids)
            basis_kinds = sorted(
                (item.kind for item in review.submission.gold_basis), key=str
            )
            graph_path_status = review.submission.graph_path_status
            root_cause = review.submission.root_cause
        b5_ids = (
            set(observation.b5_snapshot.candidate_openalex_ids)
            if observation.b5_snapshot
            else set()
        )
        missed_ids = sorted(set(gold_ids) - b5_ids)
        return M9P0PublicRecord(
            record_id=observation.record_id,
            sequence_id=observation.sequence_id,
            question_sha256=observation.question_sha256,
            eligibility=observation.eligibility,
            eligibility_reason=observation.eligibility_reason,
            stratum=observation.stratum,
            b5_snapshot=observation.b5_snapshot,
            review_status=status,
            review_revision=revision,
            review_entry_sha256=review_entry_sha256,
            gold_candidate_ids=gold_ids,
            gold_in_frozen_corpus_ids=frozen_ids,
            gold_basis_kinds=basis_kinds,
            b5_missed_ids=missed_ids,
            graph_path_status=graph_path_status,
            root_cause=root_cause,
        )

    @staticmethod
    def _observation_from_row(row: sqlite3.Row) -> M9StoredObservation:
        snapshot_json = row["b5_snapshot_json"]
        snapshot = (
            M9B5Snapshot.model_validate_json(str(snapshot_json))
            if snapshot_json is not None
            else None
        )
        return M9StoredObservation(
            record_id=str(row["record_id"]),
            sequence_id=int(row["sequence_id"]),
            source_event_sha256=str(row["source_event_sha256"]),
            sample_origin=SampleOrigin(str(row["sample_origin"])),
            question=str(row["question"]),
            question_sha256=str(row["question_sha256"]),
            eligibility=Eligibility(str(row["eligibility"])),
            eligibility_reason=EligibilityReason(str(row["eligibility_reason"])),
            stratum=(QueryStratum(str(row["stratum"])) if row["stratum"] else None),
            b5_snapshot=snapshot,
            capture_sha256=str(row["capture_sha256"]),
            previous_record_sha256=str(row["previous_record_sha256"]),
            record_sha256=str(row["record_sha256"]),
            captured_at=datetime.fromisoformat(str(row["captured_at"])),
        )

    @staticmethod
    def _review_from_row(row: sqlite3.Row) -> M9StoredReview:
        return M9StoredReview(
            submission=M9ReviewSubmission.model_validate_json(str(row["submission_json"])),
            revision=int(row["revision"]),
            review_sha256=str(row["review_sha256"]),
            previous_review_sha256=str(row["previous_review_sha256"]),
            review_entry_sha256=str(row["review_entry_sha256"]),
            reviewed_at=datetime.fromisoformat(str(row["reviewed_at"])),
        )


M9_P0_PUBLIC_CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        M9B5Snapshot,
        M9P0PublicRecord,
        M9P0Counts,
        M9P0Criteria,
        M9P0Usage,
        M9P0PublicReport,
    )
}
