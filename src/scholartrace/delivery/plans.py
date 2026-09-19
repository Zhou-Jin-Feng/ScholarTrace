"""Research plan persistence, versioning and the two-stage approval gate.

F02 in the S4-A0 review: approval was a bare event append, ``modify`` changed
nothing, and the frontend approved immediately after creation. This module makes
the plan a durable, versioned, reviewable object and splits approval into the two
gates the interface design requires:

* **Gate A** — acknowledge the cost of *producing* a plan, before generation.
  Coordinator planning itself can be billable (ADR-008), so the acknowledgement
  must precede generation, not follow it.
* **Gate B** — approve a *specific* plan version for execution, identified by
  version number and content digest. A stale digest refuses with
  ``plan_version_stale`` and executes nothing.

``modify`` supersedes the reviewed version with a new one and returns to
``waiting_approval``; it never mutates a version in place and never queues work.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from scholartrace.contracts import ResearchSubquestion
from scholartrace.delivery.budget import validate_budget_values
from scholartrace.delivery.transactions import transaction

#: M2's frozen evidence protocol accepts 3-5 papers per batch.
MIN_PAPERS = 3
MAX_PAPERS = 5


class PlanOrigin(StrEnum):
    FIXTURE = "fixture"
    API_STRONG = "api-strong"
    MANUAL = "manual"


class PlanApprovalState(StrEnum):
    WAITING_APPROVAL = "waiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class PlanError(ValueError):
    """Base class for plan contract violations, carrying a stable code."""

    code = "plan_error"


class PlanNotFoundError(PlanError):
    code = "plan_not_found"


class PlanVersionStaleError(PlanError):
    code = "plan_version_stale"


class PlanCostNotAcknowledgedError(PlanError):
    code = "plan_cost_not_acknowledged"


class PaperCountOutOfRangeError(PlanError):
    code = "paper_count_out_of_range"


@dataclass(frozen=True)
class SourceScope:
    providers: tuple[str, ...]
    year_from: int
    year_to: int
    min_papers: int = MIN_PAPERS
    max_papers: int = MAX_PAPERS

    def validate(self) -> None:
        if not self.providers:
            raise PlanError("a plan needs at least one source provider")
        if self.year_from > self.year_to:
            raise PlanError("year_from must not exceed year_to")
        # Out of range is an explicit refusal, never a silent clamp: the frozen
        # M2 protocol is not adjusted to fit a request.
        if not (MIN_PAPERS <= self.min_papers <= self.max_papers <= MAX_PAPERS):
            raise PaperCountOutOfRangeError(
                f"paper count must satisfy {MIN_PAPERS} <= min <= max <= {MAX_PAPERS}; "
                f"got min={self.min_papers}, max={self.max_papers}"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "providers": list(self.providers),
            "year_from": self.year_from,
            "year_to": self.year_to,
            "min_papers": self.min_papers,
            "max_papers": self.max_papers,
        }


@dataclass(frozen=True)
class BudgetPlan:
    max_cny: float
    max_api_calls: int
    max_wall_clock_seconds: int
    estimate_source: str
    #: False until reconciled against a real provider bill. A reference estimate
    #: is never presented as an actual charge.
    is_actual_bill: bool = False

    def __post_init__(self) -> None:
        validate_budget_values(self.max_cny, self.max_api_calls, self.max_wall_clock_seconds)
        if self.is_actual_bill:
            raise PlanError("a budget estimate cannot be a provider bill")

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_cny": self.max_cny,
            "max_api_calls": self.max_api_calls,
            "max_wall_clock_seconds": self.max_wall_clock_seconds,
            "estimate_source": self.estimate_source,
            "is_actual_bill": self.is_actual_bill,
        }


class PlanDetails(BaseModel):
    """Coordinator constraints that remain visible and bound to every approval."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    title: str = Field(min_length=3, max_length=300)
    objective: str = Field(min_length=1, max_length=2000)
    inclusion_criteria: tuple[str, ...] = Field(min_length=1, max_length=20)
    subquestions: tuple[ResearchSubquestion, ...] = Field(min_length=1, max_length=20)
    retrieval_cutoff: date

    def validate_alignment(self, questions: tuple[str, ...], source_scope: SourceScope) -> None:
        if questions != tuple(q.question for q in self.subquestions):
            raise PlanError("detailed subquestions must match the reviewed question list")
        if len({q.subquestion_id for q in self.subquestions}) != len(self.subquestions):
            raise PlanError("detailed subquestion identities must be unique")
        if not all(value.strip() for value in (*self.inclusion_criteria, self.objective)):
            raise PlanError("detailed plan criteria must not be blank")
        if source_scope.year_to > self.retrieval_cutoff.year:
            raise PlanError("source year exceeds the reviewed retrieval cutoff")


@dataclass(frozen=True)
class ResearchPlanRecord:
    task_id: str
    plan_version: int
    plan_digest: str
    generated_by: PlanOrigin
    generated_at: str
    approval_state: PlanApprovalState
    sub_questions: tuple[str, ...]
    source_scope: SourceScope
    exclusions: tuple[str, ...]
    budget_plan: BudgetPlan
    supersedes_version: int | None
    details: PlanDetails | None = None

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "task_id": self.task_id,
            "plan_version": self.plan_version,
            "plan_digest": self.plan_digest,
            "generated_by": self.generated_by.value,
            "generated_at": self.generated_at,
            "approval_state": self.approval_state.value,
            "sub_questions": list(self.sub_questions),
            "source_scope": self.source_scope.as_dict(),
            "exclusions": list(self.exclusions),
            "budget_plan": self.budget_plan.as_dict(),
            "supersedes_version": self.supersedes_version,
            **({"details": self.details.model_dump(mode="json")} if self.details else {}),
        }


def compute_plan_digest(
    *,
    sub_questions: tuple[str, ...],
    source_scope: SourceScope,
    exclusions: tuple[str, ...],
    budget_plan: BudgetPlan,
    details: PlanDetails | None = None,
) -> str:
    """Digest over the reviewable content of a plan.

    Deliberately excludes version number and timestamps: the digest answers
    "is this the same plan the user read?", so it must not change for reasons
    invisible to the reviewer.
    """

    payload = json.dumps(
        {
            "sub_questions": list(sub_questions),
            "source_scope": source_scope.as_dict(),
            "exclusions": list(exclusions),
            "budget_plan": budget_plan.as_dict(),
            **({"details": details.model_dump(mode="json")} if details else {}),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class PlanStore:
    """Durable plan versions, cost acknowledgements and approval records."""

    def __init__(self, connection: sqlite3.Connection, lock: threading.RLock) -> None:
        self._connection = connection
        self._lock = lock

    # ---- Gate A: cost acknowledgement before generation --------------------

    def acknowledge_plan_cost(
        self,
        *,
        task_id: str,
        acknowledged_max_cny: float,
        estimate_source: str,
    ) -> dict[str, Any]:
        try:
            validate_budget_values(acknowledged_max_cny, 0)
        except ValueError as exc:
            raise PlanError(str(exc)) from exc
        with self._lock, transaction(self._connection):
            self._connection.execute(
                "INSERT INTO plan_cost_acknowledgements "
                "(task_id, acknowledged_max_cny, estimate_source, acknowledged_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(task_id) DO UPDATE SET "
                "acknowledged_max_cny = excluded.acknowledged_max_cny, "
                "estimate_source = excluded.estimate_source, "
                "acknowledged_at = excluded.acknowledged_at",
                (task_id, float(acknowledged_max_cny), estimate_source, _now()),
            )
        return {
            "task_id": task_id,
            "acknowledged_max_cny": float(acknowledged_max_cny),
            "estimate_source": estimate_source,
        }

    def cost_acknowledgement(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT acknowledged_max_cny, estimate_source, acknowledged_at "
                "FROM plan_cost_acknowledgements WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "acknowledged_max_cny": float(row[0]),
            "estimate_source": str(row[1]),
            "acknowledged_at": str(row[2]),
        }

    # ---- Plan versions ----------------------------------------------------

    def save_plan(
        self,
        *,
        task_id: str,
        generated_by: PlanOrigin,
        sub_questions: tuple[str, ...],
        source_scope: SourceScope,
        exclusions: tuple[str, ...],
        budget_plan: BudgetPlan,
        estimated_cost_cny: float = 0.0,
        require_acknowledgement: bool = True,
        details: PlanDetails | None = None,
    ) -> ResearchPlanRecord:
        """Persist a new plan version, superseding any previous one.

        ``require_acknowledgement`` enforces Gate A. It is only relaxed for
        deterministic fixture plans that provably cost nothing.
        """

        validate_budget_values(estimated_cost_cny, 0)
        source_scope.validate()
        if details is not None:
            details.validate_alignment(sub_questions, source_scope)
        if not sub_questions:
            raise PlanError("a plan needs at least one sub-question")

        if require_acknowledgement:
            ack = self.cost_acknowledgement(task_id)
            if ack is None:
                raise PlanCostNotAcknowledgedError(
                    "plan generation cost was not acknowledged before generation"
                )
            if estimated_cost_cny > ack["acknowledged_max_cny"]:
                raise PlanCostNotAcknowledgedError(
                    "estimated plan generation cost exceeds the acknowledged ceiling; "
                    "re-acknowledge before generating"
                )

        digest = compute_plan_digest(
            sub_questions=sub_questions,
            source_scope=source_scope,
            exclusions=exclusions,
            budget_plan=budget_plan,
            details=details,
        )
        with self._lock, transaction(self._connection):
            row = self._connection.execute(
                "SELECT MAX(plan_version) FROM plans WHERE task_id = ?", (task_id,)
            ).fetchone()
            previous = int(row[0]) if row is not None and row[0] is not None else None
            version = 1 if previous is None else previous + 1
            if previous is not None:
                self._connection.execute(
                    "UPDATE plans SET approval_state = ? WHERE task_id = ? AND approval_state = ?",
                    (
                        PlanApprovalState.SUPERSEDED.value,
                        task_id,
                        PlanApprovalState.WAITING_APPROVAL.value,
                    ),
                )
            record = ResearchPlanRecord(
                task_id=task_id,
                plan_version=version,
                plan_digest=digest,
                generated_by=generated_by,
                generated_at=_now(),
                approval_state=PlanApprovalState.WAITING_APPROVAL,
                sub_questions=sub_questions,
                source_scope=source_scope,
                exclusions=exclusions,
                budget_plan=budget_plan,
                supersedes_version=previous,
                details=details,
            )
            self._connection.execute(
                "INSERT INTO plans (task_id, plan_version, plan_digest, generated_by, "
                "generated_at, approval_state, payload_json, supersedes_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    version,
                    digest,
                    generated_by.value,
                    record.generated_at,
                    record.approval_state.value,
                    json.dumps(record.as_public_dict(), ensure_ascii=False, sort_keys=True),
                    previous,
                ),
            )
        return record

    def _row_to_record(self, row: sqlite3.Row | tuple[Any, ...]) -> ResearchPlanRecord:
        payload = json.loads(row[6])
        scope = payload["source_scope"]
        budget = payload["budget_plan"]
        return ResearchPlanRecord(
            task_id=str(row[0]),
            plan_version=int(row[1]),
            plan_digest=str(row[2]),
            generated_by=PlanOrigin(str(row[3])),
            generated_at=str(row[4]),
            approval_state=PlanApprovalState(str(row[5])),
            sub_questions=tuple(payload["sub_questions"]),
            source_scope=SourceScope(
                providers=tuple(scope["providers"]),
                year_from=int(scope["year_from"]),
                year_to=int(scope["year_to"]),
                min_papers=int(scope["min_papers"]),
                max_papers=int(scope["max_papers"]),
            ),
            exclusions=tuple(payload["exclusions"]),
            budget_plan=BudgetPlan(
                max_cny=float(budget["max_cny"]),
                max_api_calls=int(budget["max_api_calls"]),
                max_wall_clock_seconds=int(budget["max_wall_clock_seconds"]),
                estimate_source=str(budget["estimate_source"]),
                is_actual_bill=bool(budget["is_actual_bill"]),
            ),
            supersedes_version=None if row[7] is None else int(row[7]),
            details=(PlanDetails.model_validate(payload["details"])
                     if payload.get("details") is not None else None),
        )

    _SELECT = (
        "SELECT task_id, plan_version, plan_digest, generated_by, generated_at, "
        "approval_state, payload_json, supersedes_version FROM plans"
    )

    def current_plan(self, task_id: str) -> ResearchPlanRecord:
        with self._lock:
            row = self._connection.execute(
                f"{self._SELECT} WHERE task_id = ? ORDER BY plan_version DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        if row is None:
            raise PlanNotFoundError(f"no plan has been generated for {task_id}")
        return self._row_to_record(row)

    def plan_versions(self, task_id: str) -> list[ResearchPlanRecord]:
        with self._lock:
            rows = self._connection.execute(
                f"{self._SELECT} WHERE task_id = ? ORDER BY plan_version ASC", (task_id,)
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    # ---- Gate B: execution approval bound to a version --------------------

    def verify_approval_target(
        self,
        *,
        task_id: str,
        plan_version: int,
        plan_digest: str,
    ) -> ResearchPlanRecord:
        """Confirm the caller is approving the plan they actually reviewed.

        Any mismatch refuses. Nothing is queued and no external call is made, so
        a concurrent regeneration cannot be approved by accident.
        """

        current = self.current_plan(task_id)
        if plan_version != current.plan_version:
            raise PlanVersionStaleError(
                f"plan v{plan_version} is stale; the current version is v{current.plan_version}"
            )
        if plan_digest != current.plan_digest:
            raise PlanVersionStaleError(
                "plan digest does not match the current version; re-read the plan before approving"
            )
        if current.approval_state is not PlanApprovalState.WAITING_APPROVAL:
            raise PlanVersionStaleError(
                f"plan v{plan_version} is {current.approval_state.value}, not awaiting approval"
            )
        return current

    def record_decision(
        self,
        *,
        task_id: str,
        plan_version: int,
        plan_digest: str,
        action: str,
        reason: str | None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Record an approval decision and move the version out of review.

        A repeated idempotency key returns the original decision instead of
        applying a second one, so a retried request cannot queue work twice.
        """

        if action not in {"approve", "modify", "reject"}:
            raise PlanError(f"unsupported approval action: {action}")
        with self._lock, transaction(self._connection):
            if idempotency_key is not None:
                existing = self._connection.execute(
                    "SELECT approval_id, task_id, plan_version, action, reason, decided_at "
                    ", plan_digest FROM plan_approvals WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing[1]) != task_id
                        or int(existing[2]) != plan_version
                        or str(existing[3]) != action
                        or existing[4] != reason
                        or str(existing[6]) != plan_digest
                    ):
                        raise PlanError("idempotency key reused for a different approval target")
                    return {
                        "approval_id": str(existing[0]),
                        "task_id": str(existing[1]),
                        "plan_version": int(existing[2]),
                        "action": str(existing[3]),
                        "reason": existing[4],
                        "decided_at": str(existing[5]),
                        "replayed": True,
                    }
            self.verify_approval_target(
                task_id=task_id, plan_version=plan_version, plan_digest=plan_digest
            )
            approval_id = f"approval:{uuid.uuid4().hex[:16]}"
            decided_at = _now()
            self._connection.execute(
                "INSERT INTO plan_approvals (approval_id, task_id, plan_version, plan_digest, "
                "action, reason, decided_at, idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    approval_id,
                    task_id,
                    plan_version,
                    plan_digest,
                    action,
                    reason,
                    decided_at,
                    idempotency_key,
                ),
            )
            # `modify` leaves the version superseded rather than approved: a new
            # version must be generated and reviewed before anything executes.
            next_state = {
                "approve": PlanApprovalState.APPROVED,
                "reject": PlanApprovalState.REJECTED,
                "modify": PlanApprovalState.SUPERSEDED,
            }[action]
            self._connection.execute(
                "UPDATE plans SET approval_state = ? WHERE task_id = ? AND plan_version = ?",
                (next_state.value, task_id, plan_version),
            )
        return {
            "approval_id": approval_id,
            "task_id": task_id,
            "plan_version": plan_version,
            "action": action,
            "reason": reason,
            "decided_at": decided_at,
            "replayed": False,
        }
