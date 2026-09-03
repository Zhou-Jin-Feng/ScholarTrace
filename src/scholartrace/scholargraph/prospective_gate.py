"""M9-P2 prospective sample ledger, blind Gold freeze, and Gate A scoring."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from scholartrace.contracts import Sha256, StableId
from scholartrace.scholargraph.real_miss import (
    Eligibility,
    EligibilityReason,
    GoldBasisKind,
    M9GoldBasis,
    QueryStratum,
    ReviewStatus,
    SampleOrigin,
)
from scholartrace.search.storage import write_model

M9_P2_PREREGISTRATION_SHA256 = "2267c1fda78d98c67aad051901a76cf3fbd6a7448c5ae551dc9fe8b187f84899"
M9_P2_INITIAL_TARGET = 30
M9_P2_MAX_TARGET = 50
M9_P2_MIN_OPPORTUNITIES = 6
M9_P2_MIN_RECOVERIES = 3
M9_P2_MIN_RECOVERY_RATE = 0.5
M9_P2_MIN_RECOVERED_STRATA = 2
M9_P2_MAX_PRECISION_DROP = 0.1
M9_P2_B5_COMMIT = "37e00cfdafbd21612de9e9c60807ed0aeac28783"
M9_P2_B7_COMMIT = "04f532704289e8188af1caae08fd0c1bfa2ae7e7"
M9_P2_B7_MANIFEST_SHA256 = "e673f2d7f405f1fec2741c4e36490fc47f7b4d35cb838853519ab1fb2df5ae58"
M9_P2_PARQUET_BUNDLE_SHA256 = "c837792525d387ff3257a3e7d0504a7ec73fc28e2f09399e96a339ef2a25617b"

PrivateText = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=4000),
]
OpenAlexId = Annotated[str, Field(pattern=r"^W[0-9]+$")]


class M9P2Error(ValueError):
    """An M9-P2 operation violates the frozen prospective protocol."""


class M9P2Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class M9P2Decision(StrEnum):
    COLLECTING = "COLLECTING"
    READY_FOR_CONDITIONS = "READY_FOR_CONDITIONS"
    COLLECT_MORE = "COLLECT_MORE"
    GO_EVIDENCE_GATE = "GO_EVIDENCE_GATE"
    NO_GO = "NO_GO"
    INCONCLUSIVE = "INCONCLUSIVE"


class M9P2Preregistration(M9P2Model):
    schema_version: Literal["1.0"]
    phase: Literal["M9-P2"]
    purpose: Literal["prospective-gate-a-preregistration"]
    sampling_not_before: datetime
    sample_origin: Literal["consecutive-real-scholartrace-subproblems"]
    initial_eligible_target: Literal[30]
    maximum_eligible_target: Literal[50]
    minimum_b5_miss_opportunities: Literal[6]
    minimum_b7_recovered_opportunities: Literal[3]
    minimum_b7_recovery_rate: float
    minimum_recovered_strata: Literal[2]
    maximum_precision_drop: float
    query_strata: list[QueryStratum]
    metric_definitions: dict[str, str]
    frozen_inputs: dict[str, str | int]
    exclusion_sources: dict[str, Sha256]
    excluded_question_sha256: list[Sha256]
    blinding: dict[str, bool]
    stopping_rules: list[str]

    @model_validator(mode="after")
    def validate_preregistration(self) -> M9P2Preregistration:
        if self.sampling_not_before.tzinfo is None:
            raise ValueError("sampling_not_before must include a timezone")
        if self.query_strata != list(QueryStratum):
            raise ValueError("query strata differ from the frozen enumeration")
        if (
            self.minimum_b7_recovery_rate != M9_P2_MIN_RECOVERY_RATE
            or self.maximum_precision_drop != M9_P2_MAX_PRECISION_DROP
        ):
            raise ValueError("floating-point thresholds differ from the frozen protocol")
        if len(self.excluded_question_sha256) != len(set(self.excluded_question_sha256)):
            raise ValueError("excluded question hashes contain duplicates")
        required_blinding = {
            "gold_before_conditions": True,
            "condition_snapshot_requires_gold_receipt": True,
            "gold_revision_forbidden_after_condition_reveal": True,
        }
        if self.blinding != required_blinding:
            raise ValueError("Gold blinding rules differ from the frozen protocol")
        expected_inputs: dict[str, str | int] = {
            "b5_commit": M9_P2_B5_COMMIT,
            "b7_commit": M9_P2_B7_COMMIT,
            "b7_algorithm_manifest_sha256": M9_P2_B7_MANIFEST_SHA256,
            "corpus_id": "openalex-rag-abstracts-2020-2025-v1",
            "document_count": 198,
            "parquet_bundle_sha256": M9_P2_PARQUET_BUNDLE_SHA256,
        }
        if self.frozen_inputs != expected_inputs:
            raise ValueError("frozen inputs differ from the P1 handoff")
        return self


class M9P2CaptureSubmission(M9P2Model):
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

    @model_validator(mode="after")
    def validate_eligibility(self) -> M9P2CaptureSubmission:
        if self.eligibility == Eligibility.GRAPH_ELIGIBLE:
            if self.eligibility_reason != EligibilityReason.WITHIN_FROZEN_CORPUS_SCOPE:
                raise ValueError("graph-eligible capture has an incompatible reason")
            if self.stratum is None:
                raise ValueError("graph-eligible capture requires a preregistered stratum")
        elif (
            self.eligibility_reason == EligibilityReason.WITHIN_FROZEN_CORPUS_SCOPE
            or self.stratum is not None
        ):
            raise ValueError("ineligible capture cannot carry graph execution fields")
        return self


class M9P2GoldSubmission(M9P2Model):
    schema_version: Literal["1.0"] = "1.0"
    record_id: Annotated[str, Field(pattern=r"^m9-p2-[0-9]{6}$")]
    expected_revision: int = Field(ge=0)
    status: ReviewStatus
    gold_candidate_ids: list[OpenAlexId] = Field(default_factory=list, max_length=50)
    gold_in_frozen_corpus_ids: list[OpenAlexId] = Field(default_factory=list, max_length=50)
    gold_basis: list[M9GoldBasis] = Field(default_factory=list, max_length=50)
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def validate_gold(self) -> M9P2GoldSubmission:
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
                raise ValueError("confirmed Gold requires one authority basis per ID")
        elif any(
            (
                self.gold_candidate_ids,
                self.gold_in_frozen_corpus_ids,
                self.gold_basis,
            )
        ):
            raise ValueError("non-confirmed Gold cannot carry candidate or authority IDs")
        if self.status == ReviewStatus.REJECTED and self.notes:
            raise ValueError("rejected Gold cannot carry notes")
        return self


class M9P2GoldFreezeReceipt(M9P2Model):
    schema_version: Literal["1.0"] = "1.0"
    purpose: Literal["m9-p2-gold-freeze-receipt"] = "m9-p2-gold-freeze-receipt"
    record_id: Annotated[str, Field(pattern=r"^m9-p2-[0-9]{6}$")]
    question_sha256: Sha256
    gold_entry_sha256: Sha256
    frozen_at: datetime
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def validate_receipt(self) -> M9P2GoldFreezeReceipt:
        frozen_at = self.frozen_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        expected = _sha256_json(
            {
                "schema_version": self.schema_version,
                "purpose": self.purpose,
                "record_id": self.record_id,
                "question_sha256": self.question_sha256,
                "gold_entry_sha256": self.gold_entry_sha256,
                "frozen_at": frozen_at,
            }
        )
        if self.receipt_sha256 != expected:
            raise ValueError("Gold freeze receipt hash is invalid")
        return self


class M9P2GraphPath(M9P2Model):
    kind: Literal["query_entity", "shared_entity", "relationship"]
    seed_document_id: str | None
    source_entity: str
    relationship_id: str | None
    relationship_description: str | None
    target_entity: str
    target_document_id: str
    matched_terms: list[str]
    score: float


class M9P2SourceRef(M9P2Model):
    rank: int = Field(ge=1, le=4)
    document_id: str
    document_name: str
    openalex_id: OpenAlexId
    title: str
    score: float
    matched_terms: list[str]
    graph_terms: list[str]
    graph_paths: list[M9P2GraphPath]
    reason_code: str | None = None
    query_relevance_score: float | None = None
    path_specificity_score: float | None = None


class M9P2VariantResult(M9P2Model):
    variant: Literal["B5", "B7"]
    status: Literal["succeeded", "no_match"]
    reason: Literal["matched", "no_catalog_match"]
    source_refs: list[M9P2SourceRef] = Field(max_length=4)

    @model_validator(mode="after")
    def validate_result(self) -> M9P2VariantResult:
        if [item.rank for item in self.source_refs] != list(range(1, len(self.source_refs) + 1)):
            raise ValueError(f"{self.variant} ranks are not contiguous")
        candidate_ids = [item.openalex_id for item in self.source_refs]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError(f"{self.variant} candidates contain duplicates")
        if self.status == "succeeded" and (not self.source_refs or self.reason != "matched"):
            raise ValueError(f"{self.variant} successful result is inconsistent")
        if self.status == "no_match" and (self.source_refs or self.reason != "no_catalog_match"):
            raise ValueError(f"{self.variant} no-match result is inconsistent")
        return self

    @property
    def candidate_ids(self) -> list[str]:
        return [item.openalex_id for item in self.source_refs]


class M9P2B5Algorithm(M9P2Model):
    id: Literal["m8-b5-title-text-top3"]
    commit: Literal["37e00cfdafbd21612de9e9c60807ed0aeac28783"]
    config_sha256: Sha256
    top_k: Literal[3]


class M9P2B7Algorithm(M9P2Model):
    id: Literal["m9-b7-query-relevance-graph-path-specificity"]
    commit: Literal["04f532704289e8188af1caae08fd0c1bfa2ae7e7"]
    manifest_sha256: Literal["e673f2d7f405f1fec2741c4e36490fc47f7b4d35cb838853519ab1fb2df5ae58"]
    max_graph_hints: Literal[1]
    max_hops: Literal[1]


class M9P2Usage(M9P2Model):
    network_calls: Literal[0] = 0
    model_calls: Literal[0] = 0
    index_writes: Literal[0] = 0
    paid_calls: Literal[0] = 0
    reference_cost_cny: Literal[0] = 0


class M9P2ConditionSnapshot(M9P2Model):
    schema_version: Literal["1.0"] = "1.0"
    purpose: Literal["m9-p2-b5-b7-condition-snapshot"]
    record_id: Annotated[str, Field(pattern=r"^m9-p2-[0-9]{6}$")]
    gold_freeze_sha256: Sha256
    question_sha256: Sha256
    corpus_id: Literal["openalex-rag-abstracts-2020-2025-v1"]
    corpus_version: Literal["formal-2026-08-26"]
    corpus_manifest_sha256: Sha256
    document_count: Literal[198]
    parquet_bundle_sha256: Literal[
        "c837792525d387ff3257a3e7d0504a7ec73fc28e2f09399e96a339ef2a25617b"
    ]
    b5_algorithm: M9P2B5Algorithm
    b7_algorithm: M9P2B7Algorithm
    b5: M9P2VariantResult
    b7: M9P2VariantResult
    source_ref_validity_pass: bool
    graph_path_validity_pass: bool
    b5_seed_preserved_pass: bool
    max_graph_hints_pass: bool
    deterministic_replay_pass: bool
    boundary_suite_sha256: Literal[
        "153b21fd9c1b331ae9dda229d06ac0be81e76e0f91c0711273c5cbd24c74a0b6"
    ]
    boundary_zero_candidates_pass: bool
    parquet_read_only_pass: bool
    usage: M9P2Usage
    snapshot_sha256: Sha256

    @model_validator(mode="after")
    def validate_snapshot(self) -> M9P2ConditionSnapshot:
        if self.b5.variant != "B5" or self.b7.variant != "B7":
            raise ValueError("condition variants are invalid")
        b5_ids = self.b5.candidate_ids
        b7_ids = self.b7.candidate_ids
        if len(b5_ids) > 3 or len(b7_ids) > 4:
            raise ValueError("condition candidate budget is invalid")
        if b7_ids[: len(b5_ids)] != b5_ids or len(b7_ids) > len(b5_ids) + 1:
            raise ValueError("B7 did not preserve the complete B5 seed")
        graph_hints = self.b7.source_refs[len(self.b5.source_refs) :]
        for hint in graph_hints:
            if (
                not hint.graph_paths
                or hint.reason_code != "query_relevance_plus_graph_path_specificity"
                or hint.query_relevance_score is None
                or hint.path_specificity_score is None
            ):
                raise ValueError("B7 GraphHint audit fields are incomplete")
        hash_payload = self.model_dump(mode="json", exclude={"snapshot_sha256"})
        for variant in ("b5", "b7"):
            for ref in hash_payload[variant]["source_refs"]:
                for optional_field in (
                    "reason_code",
                    "query_relevance_score",
                    "path_specificity_score",
                ):
                    if ref[optional_field] is None:
                        ref.pop(optional_field)
        expected = _sha256_json(hash_payload)
        if self.snapshot_sha256 != expected:
            raise ValueError("condition snapshot hash is invalid")
        return self


class M9P2PublicRecord(M9P2Model):
    record_id: str
    sequence_id: int
    question_sha256: Sha256
    eligibility: Eligibility
    eligibility_reason: EligibilityReason
    stratum: QueryStratum | None
    gold_status: Literal["pending", "confirmed", "rejected", "ambiguous"]
    gold_revision: int = Field(ge=0)
    gold_entry_sha256: Sha256 | None
    gold_candidate_ids: list[OpenAlexId]
    gold_in_frozen_corpus_ids: list[OpenAlexId]
    gold_basis_kinds: list[GoldBasisKind]
    condition_snapshot_sha256: Sha256 | None
    b5_candidate_ids: list[OpenAlexId]
    b7_candidate_ids: list[OpenAlexId]
    b5_missed_ids: list[OpenAlexId]
    b7_recovered_ids: list[OpenAlexId]


class M9P2Counts(M9P2Model):
    real_observations: int = Field(ge=0)
    fixture_observations_excluded: int = Field(ge=0)
    eligible_real_observations: int = Field(ge=0, le=50)
    ineligible_real_observations: int = Field(ge=0)
    gold_frozen_eligible: int = Field(ge=0)
    condition_snapshots: int = Field(ge=0)
    scored_confirmed_records: int = Field(ge=0)
    b5_miss_opportunities: int = Field(ge=0)
    b7_recovered_opportunities: int = Field(ge=0)
    recovered_strata: int = Field(ge=0)


class M9P2Metrics(M9P2Model):
    expected_candidate_count: int = Field(ge=0)
    b5_candidate_hits: int = Field(ge=0)
    b7_candidate_hits: int = Field(ge=0)
    b5_candidate_recall: float = Field(ge=0, le=1)
    b7_candidate_recall: float = Field(ge=0, le=1)
    candidate_recall_delta: float = Field(ge=-1, le=1)
    b5_candidate_precision: float = Field(ge=0, le=1)
    b7_candidate_precision: float = Field(ge=0, le=1)
    candidate_precision_delta: float = Field(ge=-1, le=1)
    b5_question_coverage: float = Field(ge=0, le=1)
    b7_question_coverage: float = Field(ge=0, le=1)
    question_coverage_delta: float = Field(ge=-1, le=1)
    b7_recovery_rate: float = Field(ge=0, le=1)


class M9P2Criteria(M9P2Model):
    initial_window_complete: bool
    all_gold_frozen_before_conditions: bool
    all_condition_snapshots_present: bool
    exclusions_enforced: bool
    capture_chain_valid: bool
    gold_chain_valid: bool
    condition_chain_valid: bool
    source_ref_validity: bool
    graph_path_validity: bool
    b5_seed_preserved: bool
    graph_hint_budget: bool
    deterministic_replay: bool
    boundary_zero_candidates: bool
    parquet_read_only: bool
    zero_external_usage: bool
    minimum_opportunities: bool
    minimum_recoveries: bool
    minimum_recovery_rate: bool
    minimum_recovered_strata: bool
    recall_strictly_higher: bool
    coverage_not_lower: bool
    precision_drop_within_limit: bool


class M9P2PublicReport(M9P2Model):
    schema_version: Literal["1.0"] = "1.0"
    purpose: Literal["m9-p2-prospective-gate-a"] = "m9-p2-prospective-gate-a"
    protocol: Literal["docs/M9_REAL_MISS_PROTOCOL.md"] = "docs/M9_REAL_MISS_PROTOCOL.md"
    preregistration_sha256: Sha256
    decision: M9P2Decision
    scholargraph_default_enabled: Literal[False] = False
    public_payload_contains_raw_questions: Literal[False] = False
    counts: M9P2Counts
    metrics: M9P2Metrics
    criteria: M9P2Criteria
    records: list[M9P2PublicRecord]
    blockers: list[str]
    usage: M9P2Usage = Field(default_factory=M9P2Usage)
    limitations: list[str]


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_text(_canonical_json(value))


def normalize_m9_p2_question(question: str) -> str:
    normalized = unicodedata.normalize("NFKC", question).casefold()
    return " ".join(normalized.split())


def m9_p2_question_sha256(question: str) -> str:
    return _sha256_text(normalize_m9_p2_question(question))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_m9_p2_preregistration(path: Path) -> M9P2Preregistration:
    if _file_sha256(path) != M9_P2_PREREGISTRATION_SHA256:
        raise M9P2Error("M9-P2 preregistration differs from the frozen manifest")
    return M9P2Preregistration.model_validate_json(path.read_text("utf-8"))


def require_project_agent_path(path: Path, *, project_root: Path) -> Path:
    resolved = path.resolve()
    agent_root = (project_root.resolve() / "agent").resolve()
    try:
        resolved.relative_to(agent_root)
    except ValueError as exc:
        raise M9P2Error("private M9-P2 data must stay below project agent/") from exc
    return resolved


def _now() -> datetime:
    return datetime.now(UTC)


class M9P2Store:
    """Append-only private ledger that enforces batch blindness and stop rules."""

    def __init__(
        self,
        path: Path,
        *,
        project_root: Path,
        preregistration_path: Path,
    ) -> None:
        self.path = require_project_agent_path(path, project_root=project_root)
        self.preregistration = load_m9_p2_preregistration(preregistration_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS captures (
                sequence_id INTEGER PRIMARY KEY,
                record_id TEXT NOT NULL UNIQUE,
                source_event_sha256 TEXT NOT NULL UNIQUE,
                sample_origin TEXT NOT NULL,
                question TEXT NOT NULL,
                question_sha256 TEXT NOT NULL UNIQUE,
                eligibility TEXT NOT NULL,
                eligibility_reason TEXT NOT NULL,
                stratum TEXT,
                capture_sha256 TEXT NOT NULL,
                previous_entry_sha256 TEXT NOT NULL,
                entry_sha256 TEXT NOT NULL UNIQUE,
                captured_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gold_reviews (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id TEXT NOT NULL REFERENCES captures(record_id),
                revision INTEGER NOT NULL,
                submission_json TEXT NOT NULL,
                submission_sha256 TEXT NOT NULL,
                previous_entry_sha256 TEXT NOT NULL,
                entry_sha256 TEXT NOT NULL UNIQUE,
                frozen_at TEXT NOT NULL,
                UNIQUE(record_id, revision)
            );
            CREATE TABLE IF NOT EXISTS conditions (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id TEXT NOT NULL UNIQUE REFERENCES captures(record_id),
                snapshot_json TEXT NOT NULL,
                snapshot_sha256 TEXT NOT NULL UNIQUE,
                previous_entry_sha256 TEXT NOT NULL,
                entry_sha256 TEXT NOT NULL UNIQUE,
                attached_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def _eligible_real_count(self) -> int:
        row = self.connection.execute(
            """
            SELECT COUNT(*) AS count FROM captures
            WHERE sample_origin = ? AND eligibility = ?
            """,
            (SampleOrigin.REAL.value, Eligibility.GRAPH_ELIGIBLE.value),
        ).fetchone()
        return int(row["count"])

    def _condition_count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM conditions").fetchone()
        return int(row["count"])

    def capture(self, submission: M9P2CaptureSubmission) -> tuple[str, int, str]:
        now = _now()
        if now < self.preregistration.sampling_not_before.astimezone(UTC):
            raise M9P2Error("capture predates the frozen prospective sampling boundary")
        question_sha256 = m9_p2_question_sha256(submission.question)
        if question_sha256 in set(self.preregistration.excluded_question_sha256):
            raise M9P2Error("question reuses an excluded M9-P0 or M8 holdout sample")
        source_event_sha256 = _sha256_text(submission.source_event_id)
        capture_payload = {
            "source_event_sha256": source_event_sha256,
            "sample_origin": submission.sample_origin.value,
            "question": submission.question,
            "question_sha256": question_sha256,
            "eligibility": submission.eligibility.value,
            "eligibility_reason": submission.eligibility_reason.value,
            "stratum": submission.stratum.value if submission.stratum else None,
        }
        capture_sha256 = _sha256_json(capture_payload)
        existing = self.connection.execute(
            "SELECT * FROM captures WHERE source_event_sha256 = ?",
            (source_event_sha256,),
        ).fetchone()
        if existing is not None:
            if str(existing["capture_sha256"]) != capture_sha256:
                raise M9P2Error("source event was reused with different content")
            return (
                str(existing["record_id"]),
                int(existing["sequence_id"]),
                str(existing["entry_sha256"]),
            )
        duplicate = self.connection.execute(
            "SELECT record_id FROM captures WHERE question_sha256 = ?",
            (question_sha256,),
        ).fetchone()
        if duplicate is not None:
            raise M9P2Error(
                f"duplicate normalized question already captured as {duplicate['record_id']}"
            )

        eligible_count = self._eligible_real_count()
        if submission.sample_origin == SampleOrigin.REAL and eligible_count >= 30:
            decision = self.public_report().decision
            allowed_to_extend = (
                eligible_count == M9_P2_INITIAL_TARGET and decision == M9P2Decision.COLLECT_MORE
            ) or (
                M9_P2_INITIAL_TARGET < eligible_count < M9_P2_MAX_TARGET
                and decision == M9P2Decision.COLLECTING
            )
            if not allowed_to_extend:
                raise M9P2Error("the current P2 batch is not authorized to collect more")
        if (
            submission.sample_origin == SampleOrigin.REAL
            and submission.eligibility == Eligibility.GRAPH_ELIGIBLE
            and eligible_count >= M9_P2_MAX_TARGET
        ):
            raise M9P2Error("M9-P2 reached the frozen 50-question eligible limit")

        last = self.connection.execute(
            "SELECT sequence_id, entry_sha256 FROM captures ORDER BY sequence_id DESC LIMIT 1"
        ).fetchone()
        sequence_id = int(last["sequence_id"]) + 1 if last is not None else 1
        previous = str(last["entry_sha256"]) if last is not None else "0" * 64
        record_id = f"m9-p2-{sequence_id:06d}"
        entry_sha256 = _sha256_json(
            {
                "sequence_id": sequence_id,
                "record_id": record_id,
                "capture_sha256": capture_sha256,
                "previous_entry_sha256": previous,
                "captured_at": now.isoformat(),
            }
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO captures VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    capture_sha256,
                    previous,
                    entry_sha256,
                    now.isoformat(),
                ),
            )
        return record_id, sequence_id, entry_sha256

    def freeze_gold(self, submission: M9P2GoldSubmission) -> M9P2GoldFreezeReceipt:
        capture = self.connection.execute(
            "SELECT * FROM captures WHERE record_id = ?", (submission.record_id,)
        ).fetchone()
        if capture is None:
            raise M9P2Error("Gold record does not exist")
        if str(capture["eligibility"]) != Eligibility.GRAPH_ELIGIBLE.value:
            raise M9P2Error("ineligible records cannot receive Gold")
        latest = self.connection.execute(
            """
            SELECT * FROM gold_reviews WHERE record_id = ?
            ORDER BY revision DESC LIMIT 1
            """,
            (submission.record_id,),
        ).fetchone()
        if self._condition_count() > 0 and latest is not None:
            raise M9P2Error("Gold cannot change after any condition reveal in the batch")
        current_revision = int(latest["revision"]) if latest is not None else 0
        if submission.expected_revision != current_revision:
            raise M9P2Error(
                f"Gold revision conflict: expected {submission.expected_revision}, "
                f"current {current_revision}"
            )
        revision = current_revision + 1
        now = _now()
        submission_json = submission.model_dump_json()
        submission_sha256 = _sha256_text(submission_json)
        previous_row = self.connection.execute(
            "SELECT entry_sha256 FROM gold_reviews ORDER BY entry_id DESC LIMIT 1"
        ).fetchone()
        previous = str(previous_row["entry_sha256"]) if previous_row is not None else "0" * 64
        entry_sha256 = _sha256_json(
            {
                "record_id": submission.record_id,
                "revision": revision,
                "submission_sha256": submission_sha256,
                "previous_entry_sha256": previous,
                "frozen_at": now.isoformat(),
            }
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO gold_reviews (
                    record_id, revision, submission_json, submission_sha256,
                    previous_entry_sha256, entry_sha256, frozen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    submission.record_id,
                    revision,
                    submission_json,
                    submission_sha256,
                    previous,
                    entry_sha256,
                    now.isoformat(),
                ),
            )
        return self._receipt(
            record_id=submission.record_id,
            question_sha256=str(capture["question_sha256"]),
            gold_entry_sha256=entry_sha256,
            frozen_at=now,
        )

    def _receipt(
        self,
        *,
        record_id: str,
        question_sha256: str,
        gold_entry_sha256: str,
        frozen_at: datetime,
    ) -> M9P2GoldFreezeReceipt:
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "purpose": "m9-p2-gold-freeze-receipt",
            "record_id": record_id,
            "question_sha256": question_sha256,
            "gold_entry_sha256": gold_entry_sha256,
            "frozen_at": frozen_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
        payload["receipt_sha256"] = _sha256_json(payload)
        return M9P2GoldFreezeReceipt.model_validate(payload)

    def attach_condition(self, snapshot: M9P2ConditionSnapshot) -> str:
        capture = self.connection.execute(
            "SELECT * FROM captures WHERE record_id = ?", (snapshot.record_id,)
        ).fetchone()
        if capture is None:
            raise M9P2Error("condition record does not exist")
        if str(capture["eligibility"]) != Eligibility.GRAPH_ELIGIBLE.value:
            raise M9P2Error("ineligible records cannot receive condition snapshots")
        eligible_count = self._eligible_real_count()
        gold_count = self._latest_gold_count()
        if eligible_count < M9_P2_INITIAL_TARGET or gold_count != eligible_count:
            raise M9P2Error("the complete eligible batch must freeze Gold before reveal")
        if M9_P2_INITIAL_TARGET < eligible_count < M9_P2_MAX_TARGET:
            raise M9P2Error("the extension batch must reach 50 before condition reveal")
        latest = self.connection.execute(
            """
            SELECT * FROM gold_reviews WHERE record_id = ?
            ORDER BY revision DESC LIMIT 1
            """,
            (snapshot.record_id,),
        ).fetchone()
        if latest is None:
            raise M9P2Error("condition snapshot has no frozen Gold")
        receipt = self._receipt(
            record_id=snapshot.record_id,
            question_sha256=str(capture["question_sha256"]),
            gold_entry_sha256=str(latest["entry_sha256"]),
            frozen_at=datetime.fromisoformat(str(latest["frozen_at"])),
        )
        if snapshot.question_sha256 != str(capture["question_sha256"]):
            raise M9P2Error("condition snapshot belongs to a different question")
        if snapshot.gold_freeze_sha256 != receipt.receipt_sha256:
            raise M9P2Error("condition snapshot does not bind the latest Gold freeze")
        existing = self.connection.execute(
            "SELECT * FROM conditions WHERE record_id = ?", (snapshot.record_id,)
        ).fetchone()
        if existing is not None:
            if str(existing["snapshot_sha256"]) != snapshot.snapshot_sha256:
                raise M9P2Error("condition snapshot is immutable after reveal")
            return str(existing["entry_sha256"])

        now = _now()
        previous_row = self.connection.execute(
            "SELECT entry_sha256 FROM conditions ORDER BY entry_id DESC LIMIT 1"
        ).fetchone()
        previous = str(previous_row["entry_sha256"]) if previous_row is not None else "0" * 64
        entry_sha256 = _sha256_json(
            {
                "record_id": snapshot.record_id,
                "snapshot_sha256": snapshot.snapshot_sha256,
                "previous_entry_sha256": previous,
                "attached_at": now.isoformat(),
            }
        )
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO conditions (
                    record_id, snapshot_json, snapshot_sha256,
                    previous_entry_sha256, entry_sha256, attached_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.record_id,
                    snapshot.model_dump_json(),
                    snapshot.snapshot_sha256,
                    previous,
                    entry_sha256,
                    now.isoformat(),
                ),
            )
        return entry_sha256

    def _latest_gold_count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(DISTINCT record_id) AS count FROM gold_reviews"
        ).fetchone()
        return int(row["count"])

    def _latest_gold(self, record_id: str) -> sqlite3.Row | None:
        row = self.connection.execute(
            """
            SELECT * FROM gold_reviews WHERE record_id = ?
            ORDER BY revision DESC LIMIT 1
            """,
            (record_id,),
        ).fetchone()
        return cast(sqlite3.Row | None, row)

    def verify_integrity(self) -> tuple[bool, bool, bool]:
        capture_valid = True
        previous = "0" * 64
        for row in self.connection.execute(
            "SELECT * FROM captures ORDER BY sequence_id"
        ).fetchall():
            capture_payload = {
                "source_event_sha256": str(row["source_event_sha256"]),
                "sample_origin": str(row["sample_origin"]),
                "question": str(row["question"]),
                "question_sha256": str(row["question_sha256"]),
                "eligibility": str(row["eligibility"]),
                "eligibility_reason": str(row["eligibility_reason"]),
                "stratum": str(row["stratum"]) if row["stratum"] is not None else None,
            }
            expected_capture = _sha256_json(capture_payload)
            expected_entry = _sha256_json(
                {
                    "sequence_id": int(row["sequence_id"]),
                    "record_id": str(row["record_id"]),
                    "capture_sha256": expected_capture,
                    "previous_entry_sha256": previous,
                    "captured_at": str(row["captured_at"]),
                }
            )
            if (
                str(row["capture_sha256"]) != expected_capture
                or str(row["previous_entry_sha256"]) != previous
                or str(row["entry_sha256"]) != expected_entry
                or str(row["question_sha256"]) != m9_p2_question_sha256(str(row["question"]))
            ):
                capture_valid = False
            previous = str(row["entry_sha256"])

        gold_valid = True
        previous = "0" * 64
        for row in self.connection.execute(
            "SELECT * FROM gold_reviews ORDER BY entry_id"
        ).fetchall():
            try:
                submission = M9P2GoldSubmission.model_validate_json(str(row["submission_json"]))
            except ValueError:
                gold_valid = False
                previous = str(row["entry_sha256"])
                continue
            submission_sha256 = _sha256_text(str(row["submission_json"]))
            expected_entry = _sha256_json(
                {
                    "record_id": str(row["record_id"]),
                    "revision": int(row["revision"]),
                    "submission_sha256": submission_sha256,
                    "previous_entry_sha256": previous,
                    "frozen_at": str(row["frozen_at"]),
                }
            )
            if (
                submission.record_id != str(row["record_id"])
                or str(row["submission_sha256"]) != submission_sha256
                or str(row["previous_entry_sha256"]) != previous
                or str(row["entry_sha256"]) != expected_entry
            ):
                gold_valid = False
            previous = str(row["entry_sha256"])

        condition_valid = True
        previous = "0" * 64
        for row in self.connection.execute("SELECT * FROM conditions ORDER BY entry_id").fetchall():
            try:
                snapshot = M9P2ConditionSnapshot.model_validate_json(str(row["snapshot_json"]))
            except ValueError:
                condition_valid = False
                previous = str(row["entry_sha256"])
                continue
            expected_entry = _sha256_json(
                {
                    "record_id": str(row["record_id"]),
                    "snapshot_sha256": snapshot.snapshot_sha256,
                    "previous_entry_sha256": previous,
                    "attached_at": str(row["attached_at"]),
                }
            )
            if (
                snapshot.record_id != str(row["record_id"])
                or str(row["snapshot_sha256"]) != snapshot.snapshot_sha256
                or str(row["previous_entry_sha256"]) != previous
                or str(row["entry_sha256"]) != expected_entry
            ):
                condition_valid = False
            previous = str(row["entry_sha256"])
        return capture_valid, gold_valid, condition_valid

    def public_report(self) -> M9P2PublicReport:
        rows = self.connection.execute("SELECT * FROM captures ORDER BY sequence_id").fetchall()
        real_rows = [row for row in rows if row["sample_origin"] == SampleOrigin.REAL.value]
        fixture_count = len(rows) - len(real_rows)
        eligible_rows = [
            row for row in real_rows if row["eligibility"] == Eligibility.GRAPH_ELIGIBLE.value
        ]
        public_records: list[M9P2PublicRecord] = []
        scored: list[tuple[sqlite3.Row, M9P2GoldSubmission, M9P2ConditionSnapshot]] = []
        gold_frozen = 0
        condition_count = 0
        condition_snapshots: list[M9P2ConditionSnapshot] = []

        for row in real_rows:
            record_id = str(row["record_id"])
            gold_row = self._latest_gold(record_id)
            gold = (
                M9P2GoldSubmission.model_validate_json(str(gold_row["submission_json"]))
                if gold_row is not None
                else None
            )
            condition_row = self.connection.execute(
                "SELECT * FROM conditions WHERE record_id = ?", (record_id,)
            ).fetchone()
            condition = (
                M9P2ConditionSnapshot.model_validate_json(str(condition_row["snapshot_json"]))
                if condition_row is not None
                else None
            )
            if row["eligibility"] == Eligibility.GRAPH_ELIGIBLE.value and gold is not None:
                gold_frozen += 1
            if condition is not None:
                condition_count += 1
                condition_snapshots.append(condition)

            gold_ids = gold.gold_candidate_ids if gold is not None else []
            frozen_gold = gold.gold_in_frozen_corpus_ids if gold is not None else []
            b5_ids = condition.b5.candidate_ids if condition is not None else []
            b7_ids = condition.b7.candidate_ids if condition is not None else []
            missed = sorted(set(frozen_gold) - set(b5_ids))
            recovered_ids = sorted(set(missed) & set(b7_ids))
            public_records.append(
                M9P2PublicRecord(
                    record_id=record_id,
                    sequence_id=int(row["sequence_id"]),
                    question_sha256=str(row["question_sha256"]),
                    eligibility=Eligibility(str(row["eligibility"])),
                    eligibility_reason=EligibilityReason(str(row["eligibility_reason"])),
                    stratum=(
                        QueryStratum(str(row["stratum"])) if row["stratum"] is not None else None
                    ),
                    gold_status=gold.status.value if gold is not None else "pending",
                    gold_revision=int(gold_row["revision"]) if gold_row is not None else 0,
                    gold_entry_sha256=(
                        str(gold_row["entry_sha256"]) if gold_row is not None else None
                    ),
                    gold_candidate_ids=gold_ids,
                    gold_in_frozen_corpus_ids=frozen_gold,
                    gold_basis_kinds=(
                        [item.kind for item in gold.gold_basis] if gold is not None else []
                    ),
                    condition_snapshot_sha256=(
                        condition.snapshot_sha256 if condition is not None else None
                    ),
                    b5_candidate_ids=b5_ids,
                    b7_candidate_ids=b7_ids,
                    b5_missed_ids=missed,
                    b7_recovered_ids=recovered_ids,
                )
            )
            if (
                gold is not None
                and gold.status == ReviewStatus.CONFIRMED
                and gold.gold_in_frozen_corpus_ids
                and condition is not None
            ):
                scored.append((row, gold, condition))

        expected_count = sum(len(gold.gold_in_frozen_corpus_ids) for _, gold, _ in scored)
        b5_hits = sum(
            len(set(gold.gold_in_frozen_corpus_ids) & set(condition.b5.candidate_ids))
            for _, gold, condition in scored
        )
        b7_hits = sum(
            len(set(gold.gold_in_frozen_corpus_ids) & set(condition.b7.candidate_ids))
            for _, gold, condition in scored
        )
        b5_returned = sum(len(condition.b5.candidate_ids) for _, _, condition in scored)
        b7_returned = sum(len(condition.b7.candidate_ids) for _, _, condition in scored)
        b5_covered = sum(
            set(gold.gold_in_frozen_corpus_ids).issubset(condition.b5.candidate_ids)
            for _, gold, condition in scored
        )
        b7_covered = sum(
            set(gold.gold_in_frozen_corpus_ids).issubset(condition.b7.candidate_ids)
            for _, gold, condition in scored
        )
        opportunities = [
            (row, gold, condition)
            for row, gold, condition in scored
            if set(gold.gold_in_frozen_corpus_ids) - set(condition.b5.candidate_ids)
        ]
        recovered_opportunities = [
            (row, gold, condition)
            for row, gold, condition in opportunities
            if (set(gold.gold_in_frozen_corpus_ids) - set(condition.b5.candidate_ids))
            & set(condition.b7.candidate_ids)
        ]
        recovered_strata = {
            str(row["stratum"])
            for row, _, _ in recovered_opportunities
            if row["stratum"] is not None
        }

        def ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
            return round(numerator / denominator, 6) if denominator else empty

        b5_recall = ratio(b5_hits, expected_count)
        b7_recall = ratio(b7_hits, expected_count)
        b5_precision = ratio(b5_hits, b5_returned, empty=1.0)
        b7_precision = ratio(b7_hits, b7_returned, empty=1.0)
        b5_coverage = ratio(b5_covered, len(scored))
        b7_coverage = ratio(b7_covered, len(scored))
        recovery_rate = ratio(len(recovered_opportunities), len(opportunities))
        capture_chain, gold_chain, condition_chain = self.verify_integrity()
        all_gold = bool(eligible_rows) and len(eligible_rows) == gold_frozen
        all_conditions = bool(eligible_rows) and len(eligible_rows) == condition_count
        integrity_values = {
            "source_ref_validity": all(
                item.source_ref_validity_pass for item in condition_snapshots
            ),
            "graph_path_validity": all(
                item.graph_path_validity_pass for item in condition_snapshots
            ),
            "b5_seed_preserved": all(item.b5_seed_preserved_pass for item in condition_snapshots),
            "graph_hint_budget": all(item.max_graph_hints_pass for item in condition_snapshots),
            "deterministic_replay": all(
                item.deterministic_replay_pass for item in condition_snapshots
            ),
            "boundary_zero_candidates": all(
                item.boundary_zero_candidates_pass for item in condition_snapshots
            ),
            "parquet_read_only": all(item.parquet_read_only_pass for item in condition_snapshots),
            "zero_external_usage": all(item.usage == M9P2Usage() for item in condition_snapshots),
        }
        criteria = M9P2Criteria(
            initial_window_complete=len(eligible_rows) >= M9_P2_INITIAL_TARGET,
            all_gold_frozen_before_conditions=all_gold,
            all_condition_snapshots_present=all_conditions,
            exclusions_enforced=True,
            capture_chain_valid=capture_chain,
            gold_chain_valid=gold_chain,
            condition_chain_valid=condition_chain,
            **integrity_values,
            minimum_opportunities=len(opportunities) >= M9_P2_MIN_OPPORTUNITIES,
            minimum_recoveries=(len(recovered_opportunities) >= M9_P2_MIN_RECOVERIES),
            minimum_recovery_rate=recovery_rate >= M9_P2_MIN_RECOVERY_RATE,
            minimum_recovered_strata=(len(recovered_strata) >= M9_P2_MIN_RECOVERED_STRATA),
            recall_strictly_higher=b7_recall > b5_recall,
            coverage_not_lower=b7_coverage >= b5_coverage,
            precision_drop_within_limit=(b7_precision >= b5_precision - M9_P2_MAX_PRECISION_DROP),
        )
        complete = len(eligible_rows) >= M9_P2_INITIAL_TARGET and all_gold and all_conditions
        integrity_pass = all(
            (
                capture_chain,
                gold_chain,
                condition_chain,
                *integrity_values.values(),
            )
        )
        performance_pass = all(
            (
                criteria.minimum_opportunities,
                criteria.minimum_recoveries,
                criteria.minimum_recovery_rate,
                criteria.minimum_recovered_strata,
                criteria.recall_strictly_higher,
                criteria.coverage_not_lower,
                criteria.precision_drop_within_limit,
            )
        )
        blockers: list[str] = []
        if not integrity_pass and condition_snapshots:
            decision = M9P2Decision.NO_GO
            blockers.append("Gate A1 integrity failed; ScholarGraph remains disabled.")
        elif (
            len(eligible_rows) < M9_P2_INITIAL_TARGET
            or M9_P2_INITIAL_TARGET < len(eligible_rows) < M9_P2_MAX_TARGET
            or not all_gold
        ):
            decision = M9P2Decision.COLLECTING
            target = (
                M9_P2_INITIAL_TARGET
                if len(eligible_rows) <= M9_P2_INITIAL_TARGET
                else M9_P2_MAX_TARGET
            )
            current_count = len(eligible_rows)
            blockers.append(
                f"Need a blind-frozen batch of {target} eligible records; current {current_count}."
            )
        elif not all_conditions:
            decision = M9P2Decision.READY_FOR_CONDITIONS
            blockers.append("Gold is frozen; attach every B5/B7 condition snapshot once.")
        elif len(opportunities) < M9_P2_MIN_OPPORTUNITIES:
            if len(eligible_rows) < M9_P2_MAX_TARGET:
                decision = M9P2Decision.COLLECT_MORE
                blockers.append(
                    "Fewer than 6 B5 miss opportunities; collect the consecutive extension batch."
                )
            else:
                decision = M9P2Decision.INCONCLUSIVE
                blockers.append("Fewer than 6 B5 miss opportunities after 50 eligible records.")
        elif performance_pass:
            decision = M9P2Decision.GO_EVIDENCE_GATE
            blockers.append("P3 still requires separate owner approval and a paid budget quote.")
        else:
            decision = M9P2Decision.NO_GO
            blockers.append("Gate A2 candidate-benefit criteria were not all satisfied.")
        if not complete and decision not in {
            M9P2Decision.COLLECTING,
            M9P2Decision.READY_FOR_CONDITIONS,
        }:
            blockers.append("The current prospective batch is incomplete.")

        return M9P2PublicReport(
            preregistration_sha256=M9_P2_PREREGISTRATION_SHA256,
            decision=decision,
            counts=M9P2Counts(
                real_observations=len(real_rows),
                fixture_observations_excluded=fixture_count,
                eligible_real_observations=len(eligible_rows),
                ineligible_real_observations=len(real_rows) - len(eligible_rows),
                gold_frozen_eligible=gold_frozen,
                condition_snapshots=condition_count,
                scored_confirmed_records=len(scored),
                b5_miss_opportunities=len(opportunities),
                b7_recovered_opportunities=len(recovered_opportunities),
                recovered_strata=len(recovered_strata),
            ),
            metrics=M9P2Metrics(
                expected_candidate_count=expected_count,
                b5_candidate_hits=b5_hits,
                b7_candidate_hits=b7_hits,
                b5_candidate_recall=b5_recall,
                b7_candidate_recall=b7_recall,
                candidate_recall_delta=round(b7_recall - b5_recall, 6),
                b5_candidate_precision=b5_precision,
                b7_candidate_precision=b7_precision,
                candidate_precision_delta=round(b7_precision - b5_precision, 6),
                b5_question_coverage=b5_coverage,
                b7_question_coverage=b7_coverage,
                question_coverage_delta=round(b7_coverage - b5_coverage, 6),
                b7_recovery_rate=recovery_rate,
            ),
            criteria=criteria,
            records=public_records,
            blockers=blockers,
            limitations=[
                "Gate A measures candidate discovery, not Evidence or final answer quality.",
                "Ambiguous and rejected Gold records stay in the stream but are not scored.",
                "The corpus is limited to 198 English RAG abstracts from 2020-2025.",
                "Proxy Gold review is not an independent blind-review agreement measure.",
            ],
        )

    def write_public_report(self, path: Path) -> str:
        report = self.public_report()
        write_model(path, report)
        return _file_sha256(path)


def build_condition_snapshot(payload: Mapping[str, Any]) -> M9P2ConditionSnapshot:
    return M9P2ConditionSnapshot.model_validate(dict(payload))
