"""M6 task service built around durable metadata and the existing event ledger."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import threading
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

from scholartrace.contracts import ResearchPlan
from scholartrace.delivery.authorization import (
    AuthorizationError,
    PhaseGrant,
    PlanningAuthorization,
    RuntimePolicy,
    TaskGrant,
)
from scholartrace.delivery.budget import BudgetReservationStore, validate_budget_values
from scholartrace.delivery.effects import EffectCancelledError, EffectJournal, EffectUncertainError
from scholartrace.delivery.evaluation import build_m6_evaluation_matrix
from scholartrace.delivery.executors import (
    DemoTaskExecutor,
    DependencyKind,
    RealExecutionUnavailableError,
    RealTaskExecutor,
    select_executor,
)
from scholartrace.delivery.executors import ExecutionMode as ExecutorMode
from scholartrace.delivery.models import (
    ApprovalRequest,
    DemoMode,
    ExecutionMode,
    ResearchPlanView,
    TaskCreateRequest,
    TaskPhase,
    TaskStatus,
)
from scholartrace.delivery.plans import (
    BudgetPlan,
    PlanDetails,
    PlanNotFoundError,
    PlanOrigin,
    PlanStore,
    SourceScope,
)
from scholartrace.delivery.queue import (
    BoundedTaskExecutor,
    QueueClosedError,
    QueueFullError,
)
from scholartrace.delivery.readiness import DependencyProbes
from scholartrace.delivery.reporting import render_html, render_markdown, render_pdf
from scholartrace.delivery.research import (
    OfflineResearchRunner,
    ResearchCancelledError,
    ResearchResult,
)
from scholartrace.delivery.store import DeliveryStore
from scholartrace.delivery.transactions import transaction
from scholartrace.delivery.views import claims_view, evidence_view
from scholartrace.workflow.models import PersistedEvent
from scholartrace.workflow.storage import RuntimeLedger

logger = logging.getLogger(__name__)


class TaskNotFoundError(KeyError):
    """The requested task or artifact does not exist."""


class TaskStateError(ValueError):
    """The requested task transition is not allowed."""


class TaskQueueFullError(TaskStateError):
    """The local bounded queue rejected a newly approved task."""


class TaskQueueClosedError(TaskStateError):
    """The local task executor is shutting down."""


class PlanVersionRequiredError(TaskStateError):
    """A decision arrived without naming the plan version it applies to.

    F02: a task that has a persisted plan may only be approved by a reviewer
    who states which version and digest they read. Accepting a bare "approve"
    would let a decision land on a plan the reviewer never saw.
    """

    code = "plan_version_required"


class RealExecutionUnavailable(TaskStateError):
    """Real execution was requested but a prerequisite is missing.

    Distinct from a task failure: nothing was attempted, and the structured
    ``gaps`` payload names each unmet dependency for the API to surface.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__("real execution is unavailable")


def _real_executor_from_environment() -> RealTaskExecutor:
    """Build the real executor from configuration only.

    Deliberately does not probe: constructing the service must not load a model
    or call a provider. Absent configuration reads as "not available", which is
    the safe direction — it produces a refusal rather than a silent demo run.
    """

    return RealTaskExecutor(
        documind_available=bool(os.environ.get("SCHOLARTRACE_DOCUMIND_URL")),
        local_model_available=bool(os.environ.get("SCHOLARTRACE_LOCAL_MODEL_URL")),
        api_strong_enabled=os.environ.get("SCHOLARTRACE_PAID_ROUTES_ENABLED", "").lower()
        in {"1", "true", "yes"}
        and bool(os.environ.get("SCHOLARTRACE_API_STRONG_KEY")),
        search_providers_configured=bool(os.environ.get("SCHOLARTRACE_SEARCH_PROVIDERS")),
        pipeline_stages_wired=False,
    )


class LiveResearchRunner(Protocol):
    """Explicit production composition; the service owns durable task stores."""

    policy: RuntimePolicy
    year_from: int
    evidence_kind: Literal["synthetic", "live"]

    def generate_plan(
        self, *, task_id: str, question: str, journal: EffectJournal,
        ledger: RuntimeLedger, cancel_event: threading.Event,
    ) -> ResearchPlan: ...

    def run(
        self, *, question: str, plan: ResearchPlanView, journal: EffectJournal,
        ledger: RuntimeLedger, cancel_event: threading.Event,
        on_event: Callable[[PersistedEvent], None],
    ) -> ResearchResult: ...


class M6TaskService:
    def __init__(
        self,
        *,
        root: Path,
        data_dir: Path | None = None,
        queue_capacity: int | None = None,
        worker_count: int | None = None,
        completion_wait_seconds: float = 0.5,
        real_executor: RealTaskExecutor | None = None,
        reservation_capacity_cny: float | None = None,
        offline_research_runner: OfflineResearchRunner | None = None,
        runtime_policy: RuntimePolicy | None = None,
        live_runner: LiveResearchRunner | None = None,
    ) -> None:
        if reservation_capacity_cny is not None:
            validate_budget_values(reservation_capacity_cny, 0)
        if live_runner is not None:
            if real_executor is not None:
                raise ValueError("choose one real execution composition")
            if (runtime_policy is not None
                    and runtime_policy.digest() != live_runner.policy.digest()):
                raise ValueError("runtime policy differs from the live composition")
            runtime_policy = live_runner.policy
        self._live_runner = live_runner
        self._planning_active: dict[str, threading.Event] = {}
        self._reservation_capacity_cny = reservation_capacity_cny
        self._offline_research_runner = offline_research_runner
        self._runtime_policy = runtime_policy
        self._dependency_probes = (
            None if runtime_policy is None else DependencyProbes(runtime_policy)
        )
        self.root = root.resolve()
        storage_root = data_dir or Path("artifacts/m6-delivery")
        if not storage_root.is_absolute():
            storage_root = self.root / storage_root
        storage_root = storage_root.resolve()
        storage_root.mkdir(parents=True, exist_ok=True)
        self.store = DeliveryStore(storage_root / "tasks.sqlite")
        self.ledger = RuntimeLedger(storage_root / "runtime.sqlite")
        self.effects = EffectJournal(storage_root / "effects.sqlite", projection_ledger=self.ledger)
        resolved_capacity = queue_capacity or int(
            os.environ.get("SCHOLARTRACE_QUEUE_CAPACITY", "4")
        )
        resolved_workers = worker_count or int(os.environ.get("SCHOLARTRACE_WORKERS", "1"))
        if completion_wait_seconds <= 0:
            raise ValueError("completion wait must be positive")
        self._completion_wait_seconds = completion_wait_seconds
        self._executor = BoundedTaskExecutor(
            max_workers=resolved_workers,
            max_queue_size=resolved_capacity,
        )
        self._state_lock = threading.RLock()
        self._closed = False
        self.plans = PlanStore(self.store.connection, self.store._lock)
        self.budget = BudgetReservationStore(self.store.connection, self.store._lock)
        # Both executors are constructed up front so the choice is an explicit
        # lookup at run time rather than an implicit fallback (F01).
        # Late-bound on purpose: resolving `_run_demo` per call keeps subclass
        # overrides and injected test doubles effective.
        self._demo_executor = DemoTaskExecutor(
            lambda task_id, *, cancel_event: (
                self._run_offline_research(task_id, cancel_event)
                if self._offline_research_runner is not None
                else self._run_demo(task_id, cancel_event=cancel_event)
            )
        )
        self._real_executor = real_executor or _real_executor_from_environment()
        if live_runner is not None:
            self._real_executor = RealTaskExecutor(
                documind_available=bool(runtime_policy and runtime_policy.documind_url),
                local_model_available=bool(runtime_policy and runtime_policy.local),
                api_strong_enabled=True,
                search_providers_configured=bool(
                    runtime_policy and runtime_policy.allowed_search_providers
                ),
                pipeline_stages_wired=True,
                run_research=lambda task_id, *, cancel_event: self._run_live_research(
                    task_id, cancel_event,
                ),
            )
        self._detect_interrupted_tasks()
        # T07: a reservation left open by a process that died must not silently
        # hold the ceiling. Reconcile before accepting new work, and report it.
        self._reconciled_reservations = self._reconcile_budget_on_start()

    def _detect_interrupted_tasks(self) -> None:
        # This delivery deployment owns one process-local worker queue. No task
        # is automatically replayed after process loss, especially paid effects.
        for status in (TaskStatus.RUNNING, TaskStatus.QUEUED, TaskStatus.CANCELLING):
            cursor = None
            while True:
                page = self.store.list_tasks(status=status, cursor=cursor, limit=100)
                for task in page["items"]:
                    task_id = str(task["task_id"])
                    self.store.update_task(
                        task_id,
                        status=TaskStatus.INTERRUPTED,
                        degradations=["Previous process stopped; explicit recovery is required."],
                    )
                    self.ledger.append_event(
                        stable_key=f"event:m6:{task_id}:interrupted:{uuid.uuid4().hex}",
                        task_id=task_id,
                        node="recovery",
                        kind="task_interrupted",
                        payload={"previous_status": status.value},
                    )
                cursor = page["next_cursor"]
                if cursor is None:
                    break

    def _reconcile_budget_on_start(self) -> list[dict[str, Any]]:
        """Reconcile known ledger usage without treating unknown external results as zero."""

        terminal = {
            TaskStatus.COMPLETED.value,
            TaskStatus.DEGRADED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.REJECTED.value,
            TaskStatus.INTERRUPTED.value,
        }
        try:
            open_ids = [item.task_id for item in self.budget.open_reservations()]
            if not open_ids:
                return []
            terminal_ids = set()
            complete_ids = set()
            for task_id in open_ids:
                try:
                    row = self.store.get_task(task_id)
                except KeyError:
                    # The task row is gone; the reservation cannot be attributed
                    # to anything running, so it is an orphan by definition.
                    terminal_ids.add(task_id)
                    continue
                if str(row["status"]) in terminal or row["execution_mode"] == "real":
                    terminal_ids.add(task_id)
                    if row["execution_mode"] == "demo":
                        complete_ids.add(task_id)
            reconciled = self.budget.reconcile_orphans(
                terminal_task_ids=terminal_ids,
                measured_usage=self.ledger.usage,
                known_complete_task_ids=complete_ids,
            )
        except Exception:  # noqa: BLE001 - startup must not fail on bookkeeping
            logger.exception("budget reconciliation at startup failed")
            return []

        report: list[dict[str, Any]] = []
        for item in reconciled:
            report.append(item.as_public_dict())
            self.ledger.append_event(
                stable_key=f"event:m6:{item.task_id}:budget-reconciled",
                task_id=item.task_id,
                node="budget",
                kind="budget_reservation_reconciled",
                payload={
                    "reserved_cny": item.reserved_cny,
                    "settled_cny": item.settled_cny,
                    "recorded_cny": item.recorded_cny,
                    "reconciliation_required": item.reconciliation_required,
                },
            )
        if report:
            logger.warning("reconciled %d orphaned budget reservation(s)", len(report))
        return report

    def close(self) -> None:
        if self._closed:
            return
        with self._state_lock:
            for pending in self._planning_active.values():
                pending.set()
            if self._planning_active:
                return  # A later close after the bounded planner unwinds owns the stores.
        drained = self._executor.shutdown(timeout_seconds=5.0)
        if not drained:
            logger.warning("task executor did not drain before shutdown timeout")
            return
        self._closed = True
        self.effects.close()
        self.ledger.close()
        self.store.close()

    def create_task(
        self,
        request: TaskCreateRequest,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        request_hash = hashlib.sha256(request.model_dump_json().encode("utf-8")).hexdigest()
        task_id = f"task:m6:{uuid.uuid4().hex[:16]}"
        thread_id = f"thread:m6:{uuid.uuid4().hex[:16]}"
        title = request.title or request.question[:80]
        task = self.store.create_task(
            task_id=task_id,
            thread_id=thread_id,
            title=title,
            question=request.question,
            demo_mode=request.demo_mode,
            idempotency_key=idempotency_key,
            request_sha256=request_hash,
            execution_mode=request.execution_mode,
        )
        resolved_task_id = str(task["task_id"])
        if resolved_task_id == task_id:
            self.ledger.append_event(
                stable_key=f"event:m6:{task_id}:created",
                task_id=task_id,
                node="delivery_api",
                kind="task_created",
                payload={"demo_mode": request.demo_mode.value},
            )
        return self.summary(resolved_task_id)

    def summary(self, task_id: str) -> dict[str, Any]:
        try:
            task = self.store.get_task(task_id)
        except KeyError as exc:
            raise TaskNotFoundError(task_id) from exc
        task["event_count"] = self.ledger.event_count(task_id)
        task["artifact_count"] = len(self.store.list_artifacts(task_id))
        return task

    def approve_task(self, task_id: str, request: ApprovalRequest) -> dict[str, Any]:
        # The worker waits for durable admission; a rolled-back admission never runs.
        admitted = False
        gate = threading.Event()
        submission = None
        execution_grant = None
        with self._state_lock:
            try:
                with self.store._lock, transaction(self.store.connection):
                    task = self._task(task_id)
                    if task["status"] != TaskStatus.WAITING_APPROVAL.value:
                        raise TaskStateError("only waiting_approval tasks can be approved")
                    try:
                        current = self.plans.current_plan(task_id)
                    except PlanNotFoundError:
                        current = None
                    if current is None:
                        if task["execution_mode"] == "real" or request.action == "modify":
                            raise PlanVersionRequiredError("a persisted plan is required")
                    else:
                        if request.plan_version is None or request.plan_digest is None:
                            raise PlanVersionRequiredError(
                                "approval requires plan_version and plan_digest"
                            )
                        self.plans.verify_approval_target(
                            task_id=task_id,
                            plan_version=request.plan_version,
                            plan_digest=request.plan_digest,
                        )

                    if task_id in self._planning_active:
                        raise TaskStateError("planning is still in progress")
                    if request.action == "modify":
                        if self.effects.has_authorization(task_id):
                            self.effects.revoke_phase(task_id, "execution")
                        modified = request.modified_plan
                        assert modified is not None and current is not None
                        if current.details is not None and modified.details is None:
                            raise TaskStateError(
                                "modified plan must preserve explicit detailed constraints"
                            )
                        old, new = current.budget_plan, modified.budget_plan
                        if (
                            new.max_cny > old.max_cny
                            or new.max_api_calls > old.max_api_calls
                            or new.max_wall_clock_seconds > old.max_wall_clock_seconds
                        ):
                            raise TaskStateError(
                                "modify cannot increase the reviewed budget limits"
                            )
                        self.plans.record_decision(
                            task_id=task_id,
                            plan_version=current.plan_version,
                            plan_digest=current.plan_digest,
                            action="modify",
                            reason=request.reason,
                        )
                        self.plans.save_plan(
                            task_id=task_id,
                            generated_by=PlanOrigin.MANUAL,
                            sub_questions=tuple(modified.sub_questions),
                            source_scope=modified.source_scope,
                            exclusions=tuple(modified.exclusions),
                            budget_plan=modified.budget_plan,
                            details=modified.details,
                            require_acknowledgement=False,
                        )
                    elif request.action == "reject":
                        if self.effects.has_authorization(task_id):
                            self.effects.revoke_task(task_id)
                        if current is not None:
                            self.plans.record_decision(
                                task_id=task_id,
                                plan_version=current.plan_version,
                                plan_digest=current.plan_digest,
                                action="reject",
                                reason=request.reason,
                            )
                        self.store.update_task(
                            task_id,
                            status=TaskStatus.REJECTED,
                            phase=TaskPhase.DONE,
                            degradations=[request.reason or "plan rejected by user"],
                        )
                    else:
                        if task["execution_mode"] == "real":
                            execution_grant = self._prepare_execution_authorization(
                                task_id, request,
                            )
                        else:
                            if request.execution_authorization is not None:
                                raise AuthorizationError("Demo cannot accept live authorization")
                            self._reserve_for_task(task_id)
                        if current is not None:
                            self.plans.record_decision(
                                task_id=task_id,
                                plan_version=current.plan_version,
                                plan_digest=current.plan_digest,
                                action="approve",
                                reason=request.reason,
                            )
                        self.store.update_task(
                            task_id, status=TaskStatus.QUEUED, phase=TaskPhase.PLAN
                        )

                        def execute_queued(cancel_event: threading.Event) -> None:
                            gate.wait()
                            if admitted:
                                self._execute_queued(task_id, cancel_event)

                        submission = self._executor.submit(task_id, execute_queued)
                if request.action == "approve":
                    self._record_reservation_event(task_id)
                action_kind = {
                    "modify": "plan_modified",
                    "reject": "plan_rejected",
                    "approve": "plan_approved",
                }
                self.ledger.append_event(
                    stable_key=f"event:m6:{task_id}:{action_kind[request.action]}:{request.plan_version}",
                    task_id=task_id,
                    node="approval",
                    kind=action_kind[request.action],
                    payload={"reason": request.reason or "not_provided"},
                )
                if execution_grant is not None:
                    self.effects.revoke_phase(task_id, "planning")
                    self.effects.activate_phase(execution_grant)
                admitted = True
            except QueueFullError as exc:
                raise TaskQueueFullError("task queue is full; approval may be retried") from exc
            except QueueClosedError as exc:
                raise TaskQueueClosedError(
                    "task queue is shutting down; approval was not applied"
                ) from exc
            except Exception as exc:
                if submission is not None and not admitted:
                    # The databases are physically separate. Do not dispatch if
                    # admission committed but its audit record could not be saved.
                    self.store.update_task(
                        task_id,
                        status=TaskStatus.INTERRUPTED,
                        degradations=[
                            "Admission recording failed; execution was not started. "
                            "Explicit recovery is required."
                        ],
                    )
                    self.budget.mark_unconfirmed(task_id, self.ledger.usage(task_id))
                    raise TaskStateError(
                        "admission interrupted; reload task before recovery"
                    ) from exc
                raise
            finally:
                gate.set()
        if request.action == "reject":
            self._write_exports(task_id)
        if submission is not None:
            submission.finished.wait(self._completion_wait_seconds)
        return self.summary(task_id)

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        with self._state_lock:
            task = self._task(task_id)
            status = TaskStatus(str(task["status"]))
            if status in {
                TaskStatus.COMPLETED,
                TaskStatus.DEGRADED,
                TaskStatus.REJECTED,
                TaskStatus.CANCELLED,
                TaskStatus.FAILED,
            }:
                raise TaskStateError(f"task is already terminal: {status.value}")
            pending_plan = self._planning_active.get(task_id)
            if pending_plan is not None:
                pending_plan.set()
            if self.effects.has_authorization(task_id):
                self.effects.revoke_task(task_id)
            if status in {TaskStatus.WAITING_APPROVAL, TaskStatus.INTERRUPTED}:
                return self._finish_cancelled(reason="cancelled before approval", task_id=task_id)
            submission = self._executor.cancel(task_id)
            if submission is None:
                current = TaskStatus(str(self._task(task_id)["status"]))
                raise TaskStateError(f"task is no longer cancellable: {current.value}")
            self.store.update_task(
                task_id,
                status=TaskStatus.CANCELLING,
                degradations=[
                    "Cancellation requested; the active phase will stop at its next boundary."
                ],
            )
            self.ledger.append_event(
                stable_key=f"event:m6:{task_id}:cancel-requested",
                task_id=task_id,
                node="queue",
                kind="task_cancel_requested",
            )
        submission.finished.wait(self._completion_wait_seconds)
        return self.summary(task_id)

    def resume_task(self, task_id: str, *, plan_version: int, plan_digest: str) -> dict[str, Any]:
        with self._state_lock:
            task = self._task(task_id)
            if task["status"] != TaskStatus.INTERRUPTED.value:
                raise TaskStateError("only interrupted tasks can be resumed")
            plan = self.current_plan(task_id)
            if (
                plan["plan_version"] != plan_version
                or plan["plan_digest"] != plan_digest
                or plan["approval_state"] != "approved"
            ):
                raise TaskStateError("recovery requires the exact previously approved plan")
            attempts = sum(e["kind"] == "task_resume_requested" for e in self.events(task_id))
            if attempts >= 2:
                raise TaskStateError("task recovery attempt limit reached")
            if task["execution_mode"] == "real":
                if self._live_runner is None:
                    raise TaskStateError(
                        "live recovery requires an explicit production composition"
                    )
                self.effects.reconcile_stage_resume(
                    task_id, plan_version=plan_version, plan_digest=plan_digest,
                )
            elif self._offline_research_runner is None:
                raise TaskStateError("unjournaled recovery is not enabled")
            admitted = threading.Event()
            gate = threading.Event()

            def execute(cancel: threading.Event) -> None:
                gate.wait()
                if admitted.is_set():
                    self._execute_queued(task_id, cancel)

            try:
                with self.store._lock, transaction(self.store.connection):
                    self.store.update_task(task_id, status=TaskStatus.QUEUED)
                    submission = self._executor.submit(task_id, execute)
                self.ledger.append_event(
                    stable_key=f"event:m6:{task_id}:resume:{attempts + 1}",
                    task_id=task_id,
                    node="recovery",
                    kind="task_resume_requested",
                    payload={"plan_version": plan_version, "attempt": attempts + 1},
                )
                admitted.set()
            except QueueFullError as exc:
                raise TaskQueueFullError("task queue is full") from exc
            except QueueClosedError as exc:
                raise TaskQueueClosedError("task queue is closed") from exc
            except Exception:
                self.store.update_task(task_id, status=TaskStatus.INTERRUPTED)
                raise
            finally:
                gate.set()
        submission.finished.wait(self._completion_wait_seconds)
        return self.summary(task_id)

    def list_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        self._task(task_id)
        return self.store.list_artifacts(task_id)

    def research_result(self, task_id: str) -> ResearchResult:
        self._task(task_id)
        try:
            artifact = self.artifact(task_id, f"artifact:m6:{task_id}:research-result")
        except TaskNotFoundError as exc:
            raise TaskStateError("research output is not available for this task") from exc
        return ResearchResult.model_validate_json(artifact["content"])

    def claims(self, task_id: str) -> dict[str, Any]:
        result = self.research_result(task_id)
        plan = ResearchPlanView.model_validate(self.current_plan(task_id))
        return claims_view(result, plan).model_dump(mode="json")

    def evidence(self, task_id: str, evidence_id: str) -> dict[str, Any]:
        self._task(task_id)
        try:
            return evidence_view(self.research_result(task_id), evidence_id).model_dump(mode="json")
        except (TaskStateError, KeyError) as exc:
            raise TaskNotFoundError(evidence_id) from exc

    def _run_offline_research(self, task_id: str, cancel_event: threading.Event) -> dict[str, Any]:
        assert self._offline_research_runner is not None
        task = self._task(task_id)
        plan = ResearchPlanView.model_validate(self.current_plan(task_id))
        if task["execution_mode"] != "demo" or plan.generated_by != "fixture":
            raise TaskStateError("offline research is only available for explicit fixture plans")
        with self._state_lock:
            if cancel_event.is_set():
                return self._finish_cancelled(task_id, reason="cancelled before research")
            self.store.update_task(task_id, status=TaskStatus.RUNNING, phase=TaskPhase.SEARCH)
        try:
            result = self.research_result(task_id)
        except TaskStateError:
            result = self._offline_research_runner(str(task["question"]), plan, cancel_event)
        if (
            result.task_id != task_id
            or result.plan_version != plan.plan_version
            or result.plan_digest != plan.plan_digest
            or result.execution_kind != "offline_fixture"
        ):
            raise TaskStateError("research output does not belong to the approved fixture task")
        with self._state_lock:
            if cancel_event.is_set():
                return self._finish_cancelled(
                    task_id, reason="cancelled before research publication"
                )
            self.store.add_artifact(
                task_id=task_id,
                artifact_id=f"artifact:m6:{task_id}:research-result",
                artifact_type="research_result",
                media_type="application/json",
                content=result.model_dump_json().encode("utf-8"),
            )
            for event in result.timeline:
                self.ledger.append_event(
                    stable_key=f"event:m6:{task_id}:research-stage:{event.sequence}",
                    task_id=task_id,
                    node=event.node,
                    kind=event.kind,
                    payload={**event.payload, "occurred_at": event.created_at},
                )
            status = {
                "succeeded": TaskStatus.COMPLETED,
                "degraded": TaskStatus.DEGRADED,
                "failed": TaskStatus.FAILED,
            }[result.outcome]
            self.store.update_task(
                task_id,
                status=status,
                phase=TaskPhase.DONE,
                metrics={
                    "execution_mode": "offline_fixture",
                    "model_calls": 0,
                    "provider_calls": 0,
                    "paper_count": len(result.papers),
                    "claim_count": len(result.claims),
                    "evidence_count": len(result.evidence),
                },
                degradations=result.limitations,
            )
            self.ledger.append_event(
                stable_key=f"event:m6:{task_id}:research-completed",
                task_id=task_id,
                node="research",
                kind="workflow_finished",
                payload={"status": status.value, "execution_mode": "offline_fixture"},
            )
        return self._write_exports(task_id)

    def artifact(self, task_id: str, artifact_id: str) -> dict[str, Any]:
        self._task(task_id)
        try:
            artifact = self.store.get_artifact(artifact_id)
        except KeyError as exc:
            raise TaskNotFoundError(artifact_id) from exc
        if artifact["task_id"] != task_id:
            raise TaskNotFoundError(artifact_id)
        return artifact

    def events(self, task_id: str) -> list[dict[str, Any]]:
        self._task(task_id)
        return [event.model_dump(mode="json") for event in self.ledger.replay(task_id=task_id)]

    def task_exists(self, task_id: str) -> bool:
        try:
            self.store.get_task(task_id)
        except KeyError:
            return False
        return True

    def evaluation_matrix(self) -> dict[str, Any]:
        return build_m6_evaluation_matrix(root=self.root).model_dump(mode="json")

    def queue_snapshot(self) -> dict[str, int | bool]:
        return self._executor.snapshot()

    # ---- Dependency readiness (T16) ----------------------------------------

    async def probe_dependencies(self, client: httpx.AsyncClient) -> dict[str, Any]:
        if not self.queue_snapshot()["accepting"]:
            raise TaskStateError("dependency checks are unavailable while draining")
        if self._dependency_probes is None:
            raise AuthorizationError("explicit runtime policy is required for dependency probes")
        await self._dependency_probes.refresh(client)
        return self.dependency_report()

    def dependency_report(self) -> dict[str, Any]:
        """Read-only readiness snapshot. Replaces the hardcoded "API ready".

        Deliberately cheap: it reports what configuration says, and never loads
        a model, calls a provider, or echoes a credential. A dependency that
        cannot be judged without an expensive probe is reported as ``unknown``
        with the reason why — not optimistically as ready.

        A missing paid-profile approval is ``disabled`` (a policy decision),
        not ``error`` (a fault), so an operator is not sent debugging a gate
        that is closed on purpose.
        """

        gaps = {gap.kind: gap for gap in self._real_executor.preflight()}
        queue = self.queue_snapshot()

        def entry(kind: DependencyKind, state_when_missing: str) -> dict[str, Any]:
            gap = gaps.get(kind)
            if gap is None:
                return {
                    "state": "unknown",
                    "configured": True,
                    "reason": "Configured but not health-checked; availability is unknown.",
                    "remediation": "Validate dependency health explicitly before real execution.",
                }
            return {
                "state": state_when_missing,
                "configured": False,
                "reason": gap.reason,
                "remediation": gap.remediation,
            }

        dependencies = {
            # Configured-but-unprobed is the honest answer for these two: a real
            # probe would load a model or hit a provider, which this must not do.
            "documind": entry(DependencyKind.DOCUMIND, "unavailable"),
            "local_model": entry(DependencyKind.LOCAL_MODEL, "unavailable"),
            "search_provider": entry(DependencyKind.SEARCH_PROVIDER, "unavailable"),
            # ADR-008/014/016: a closed paid gate is policy, not breakage.
            "api_strong": entry(DependencyKind.API_STRONG, "disabled"),
        }

        if self._dependency_probes is not None:
            dependencies = self._dependency_probes.snapshot()
        blocking = [name for name, item in dependencies.items() if item["state"] != "ready"]
        real_ready = (
            not blocking and self._real_executor.pipeline_stages_wired and queue["accepting"]
        )
        return {
            "schema_version": "1.0",
            "api": "ready" if queue["accepting"] else "draining",
            "queue": queue,
            "dependencies": dependencies,
            "demo_mode": {
                "state": "ready" if queue["accepting"] else "unavailable",
                "reason": None if queue["accepting"] else "Task queue is draining.",
                "remediation": None,
            },
            "real_mode": {
                "state": "ready" if real_ready else "unavailable",
                "blocking_dependencies": sorted(blocking),
                "pipeline_stages_wired": self._real_executor.pipeline_stages_wired,
            },
            "overall": "ready" if real_ready else "degraded",
            "probe_policy": (
                "cached metadata only: explicit bounded GET checks; no model load or generation"
                if self._dependency_probes is not None else
                "configuration-only: no model load, no provider call, no credential echoed"
            ),
        }

    # ---- Task history (T13) ------------------------------------------------

    def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        page = self.store.list_tasks(status=status, cursor=cursor, limit=limit)
        for item in page["items"]:
            item["event_count"] = self.ledger.event_count(str(item["task_id"]))
            item["artifact_count"] = len(self.store.list_artifacts(str(item["task_id"])))
        return page

    # ---- Plan gates (T06) --------------------------------------------------

    def current_plan(self, task_id: str) -> dict[str, Any]:
        self._task(task_id)
        return self.plans.current_plan(task_id).as_public_dict()

    def plan_versions(self, task_id: str) -> dict[str, Any]:
        self._task(task_id)
        return {
            "schema_version": "1.0",
            "task_id": task_id,
            "versions": [record.as_public_dict() for record in self.plans.plan_versions(task_id)],
        }

    def estimate_plan(self, task_id: str) -> dict[str, Any]:
        """Gate A estimate. A fixture plan provably costs nothing.

        The estimate is a reference figure, never presented as an actual bill.
        """

        task = self._task(task_id)
        if task["execution_mode"] == "real":
            policy = self._runtime_policy
            saved_authorization = None
            prefix = f"artifact:m6:{task_id}:planning-authorization:"
            saved = [item for item in self.store.list_artifacts(task_id)
                     if str(item["artifact_id"]).startswith(prefix)]
            if saved:
                latest = max(
                    saved, key=lambda item: int(str(item["artifact_id"]).rsplit(":", 1)[1])
                )
                saved_authorization = json.loads(self.artifact(
                    task_id, str(latest["artifact_id"])
                )["content"])
            return {
                "schema_version": "1.0", "task_id": task_id, "estimated_cny": None,
                "estimate_source": "explicit_authorization_required", "is_actual_bill": False,
                "requires_acknowledgement": True, "planner": "api-strong",
                "policy_sha256": None if policy is None else policy.digest(),
                "configured": policy is not None,
                "runtime_policy": None if policy is None else policy.model_dump(mode="json"),
                "production_composed": self._live_runner is not None,
                "planning_authorization": saved_authorization,
                "has_task_authorization": self.effects.has_authorization(task_id),
            }
        gaps = {gap.kind for gap in self._real_executor.preflight()}
        paid_planning_available = DependencyKind.API_STRONG not in gaps
        return {
            "schema_version": "1.0",
            "task_id": task_id,
            "estimated_cny": 0.0 if not paid_planning_available else None,
            "estimate_source": "fixture" if not paid_planning_available else "unavailable",
            "is_actual_bill": False,
            # A zero-cost fixture plan still requires an explicit acknowledgement
            # so the two-gate order is exercised the same way in both modes.
            "requires_acknowledgement": True,
            "planner": "fixture" if not paid_planning_available else "api-strong",
        }

    def acknowledge_plan_cost(
        self, task_id: str, *, acknowledged_max_cny: float,
        authorization: PlanningAuthorization | None = None,
    ) -> dict[str, Any]:
        task = self._task(task_id)
        if task["execution_mode"] == "real":
            return self._authorize_planning(task_id, acknowledged_max_cny, authorization)
        if authorization is not None:
            raise AuthorizationError("Demo tasks cannot receive live authorization")
        estimate = self.estimate_plan(task_id)
        result = self.plans.acknowledge_plan_cost(
            task_id=task_id,
            acknowledged_max_cny=acknowledged_max_cny,
            estimate_source=str(estimate["estimate_source"]),
        )
        self.ledger.append_event(
            stable_key=f"event:m6:{task_id}:plan-cost-acknowledged:{uuid.uuid4().hex}",
            task_id=task_id,
            node="approval",
            kind="plan_cost_acknowledged",
            payload={"acknowledged_max_cny": acknowledged_max_cny},
        )
        return {"schema_version": "1.0", **result}

    def _authorize_planning(
        self, task_id: str, acknowledged_max_cny: float,
        authorization: PlanningAuthorization | None,
    ) -> dict[str, Any]:
        with self._state_lock:
            if self._task(task_id)["status"] != TaskStatus.WAITING_APPROVAL.value:
                raise TaskStateError("planning authorization requires a waiting task")
            policy = self._runtime_policy
            if authorization is None or policy is None:
                raise AuthorizationError("real planning requires explicit configured authorization")
            if authorization.policy_sha256 != policy.digest():
                raise AuthorizationError("reviewed policy differs from configured policy")
            if Decimal(str(acknowledged_max_cny)) != Decimal(authorization.planning.max_cny):
                raise AuthorizationError("planning acknowledgement differs from phase allowance")
            total = Decimal(authorization.max_cny)
            if Decimal(str(float(total))) != total:
                raise AuthorizationError("task capacity cannot represent this amount exactly")
            remaining = (datetime.fromisoformat(authorization.deadline_at)
                         - datetime.now(UTC)).total_seconds()
            if not 0 < remaining <= authorization.max_wall_clock_seconds:
                raise AuthorizationError("task deadline must fit its original duration allowance")
            task_grant = TaskGrant(
                task_id=task_id, policy=policy, max_cny=authorization.max_cny,
                max_remote_calls=authorization.max_remote_calls,
                max_local_calls=authorization.max_local_calls,
                max_external_requests=authorization.max_external_requests,
                deadline_at=authorization.deadline_at,
            )
            acknowledgement = hashlib.sha256(authorization.model_dump_json().encode()).hexdigest()
            phase = PhaseGrant(
                task_id=task_id, authorization_id=f"planning:{authorization.generation}",
                phase="planning", generation=authorization.generation,
                policy_sha256=policy.digest(), acknowledgement_sha256=acknowledgement,
                **authorization.planning.model_dump(),
            )
            with self.store._lock, transaction(self.store.connection):
                reservation = self.budget.get(task_id)
                if reservation is None:
                    self.budget.reserve(
                        task_id=task_id, plan_version=0, plan_digest="planning:" + policy.digest(),
                        reserved_cny=float(total),
                        reserved_api_calls=authorization.max_remote_calls,
                        reserved_wall_clock_seconds=authorization.max_wall_clock_seconds,
                        estimate_source="authorized:" + policy.digest(),
                        ceiling_cny=self._reservation_capacity_cny,
                    )
                elif (not reservation.is_open or reservation.reconciliation_required
                      or Decimal(str(reservation.reserved_cny)) != total
                      or reservation.reserved_api_calls != authorization.max_remote_calls
                      or reservation.reserved_wall_clock_seconds
                      != authorization.max_wall_clock_seconds
                      or reservation.estimate_source != "authorized:" + policy.digest()):
                    raise AuthorizationError("existing task capacity cannot be replaced")
                self.effects.authorize_task(task_grant)
                self.effects.prepare_phase(phase)
                result = self.plans.acknowledge_plan_cost(
                    task_id=task_id, acknowledged_max_cny=acknowledged_max_cny,
                    estimate_source="approved_runtime_policy",
                )
                self.store.add_artifact(
                    task_id=task_id,
                    artifact_id=(f"artifact:m6:{task_id}:planning-authorization:"
                                 f"{authorization.generation}"),
                    artifact_type="planning_authorization", media_type="application/json",
                    content=authorization.model_dump_json().encode(),
                )
            self.ledger.append_event(
                stable_key=f"authorization:{task_id}:{phase.authorization_id}", task_id=task_id,
                node="approval", kind="planning_authorized",
                payload={"policy_sha256": policy.digest(),
                         "acknowledgement_sha256": acknowledgement},
            )
            self.effects.activate_phase(phase)
            return {"schema_version": "1.0", **result,
                    "authorization_id": phase.authorization_id, "policy_sha256": policy.digest()}

    def generate_plan(self, task_id: str) -> dict[str, Any]:
        """Produce a plan version after Gate A, before Gate B.

        With `api-strong` unapproved this yields a deterministic fixture plan,
        labelled as such. It is never presented as a real Coordinator plan.
        """

        if self._task(task_id)["execution_mode"] == "real" and self._live_runner is not None:
            return self._generate_live_plan(task_id)
        with self._state_lock:
            if self._task(task_id)["status"] != TaskStatus.WAITING_APPROVAL.value:
                raise TaskStateError("only waiting_approval tasks can generate plans")
            estimate = self.estimate_plan(task_id)
            origin = (
                PlanOrigin.FIXTURE if estimate["planner"] == "fixture" else PlanOrigin.API_STRONG
            )
            if origin is PlanOrigin.API_STRONG:
                # Real planning is not assembled yet; refuse rather than silently
                # substituting a fixture plan and calling it real.
                raise RealExecutionUnavailable(
                    {
                        "code": "paid_profile_not_approved",
                        "detail": "api-strong plan generation is not assembled in this build",
                    }
                )
            record = self.plans.save_plan(
                task_id=task_id,
                generated_by=origin,
                sub_questions=self._fixture_sub_questions(task_id),
                source_scope=SourceScope(
                    providers=("arxiv", "openalex", "crossref"), year_from=2020, year_to=2025
                ),
                exclusions=(),
                budget_plan=BudgetPlan(
                    max_cny=0.0,
                    max_api_calls=0,
                    max_wall_clock_seconds=1800,
                    estimate_source="fixture",
                ),
                estimated_cost_cny=float(estimate["estimated_cny"]),
            )
            self.ledger.append_event(
                stable_key=f"event:m6:{task_id}:plan-v{record.plan_version}",
                task_id=task_id,
                node="approval",
                kind="plan_generated",
                payload={
                    "plan_version": record.plan_version,
                    "plan_digest": record.plan_digest,
                    "generated_by": record.generated_by.value,
                },
            )
            return record.as_public_dict()

    def _generate_live_plan(self, task_id: str) -> dict[str, Any]:
        runner = self._live_runner
        assert runner is not None
        with self._state_lock:
            task = self._task(task_id)
            if task["status"] != TaskStatus.WAITING_APPROVAL.value:
                raise TaskStateError("only waiting_approval tasks can generate plans")
            if task_id in self._planning_active:
                raise TaskStateError("planning is already in progress")
            policy = self.effects.approved_policy(task_id)
            if policy.digest() != runner.policy.digest():
                raise AuthorizationError("planning policy differs from the approved task")
            with self.effects._lock:
                phase = self.effects.db.execute(
                    "SELECT * FROM phase_authorizations WHERE task_id=? "
                    "AND phase='planning' AND state='active'", (task_id,),
                ).fetchone()
            if phase is None:
                raise AuthorizationError("active planning authorization is required")
            phase_id = str(phase["authorization_id"])
            suffix = hashlib.sha256(phase_id.encode()).hexdigest()
            committed_id = f"artifact:m6:{task_id}:plan-commit:{suffix}"
            try:
                committed = json.loads(self.artifact(task_id, committed_id)["content"])
                record = self.plans.current_plan(task_id)
                if record.plan_version != committed["plan_version"]:
                    raise TaskStateError("a newer reviewed plan exists; planning is not repeated")
                self._record_plan_generated(task_id, record.as_public_dict())
                return record.as_public_dict()
            except TaskNotFoundError:
                pass
            cancel = threading.Event()
            self._planning_active[task_id] = cancel
        try:
            draft_id = f"artifact:m6:{task_id}:coordinator:{suffix}"
            try:
                generated = ResearchPlan.model_validate_json(
                    self.artifact(task_id, draft_id)["content"]
                )
            except TaskNotFoundError:
                generated = runner.generate_plan(
                    task_id=task_id, question=str(task["question"]), journal=self.effects,
                    ledger=self.ledger, cancel_event=cancel,
                )
            if generated.task_id != task_id or generated.question != task["question"]:
                raise TaskStateError("Coordinator returned a different task or question")
            if not set(generated.sources).issubset(policy.allowed_search_providers):
                raise AuthorizationError("Coordinator sources exceed the approved policy")
            if any(q.evidence_required != "fulltext" for q in generated.subquestions):
                raise TaskStateError("this research composition requires full-text subquestions")
            limits = generated.budget.limits
            if limits.max_fulltext_papers > policy.max_papers:
                raise AuthorizationError("Coordinator paper count exceeds the approved policy")
            total, total_calls = self.effects.approved_limits(task_id)
            if Decimal(str(limits.max_cost_cny)) > total or limits.max_api_calls > total_calls:
                raise AuthorizationError("Coordinator budget exceeds the original task envelope")
            reservation = self.budget.get(task_id)
            if (reservation is None
                    or limits.max_duration_seconds > reservation.reserved_wall_clock_seconds):
                raise AuthorizationError("Coordinator duration exceeds the original task envelope")
            details = PlanDetails(
                title=generated.title, objective=generated.objective,
                inclusion_criteria=tuple(generated.inclusion_criteria),
                subquestions=tuple(generated.subquestions),
                retrieval_cutoff=generated.retrieval_cutoff,
            )
            with self._state_lock, self.store._lock, transaction(self.store.connection):
                if cancel.is_set() or self._task(task_id)["status"] != "waiting_approval":
                    raise TaskStateError("planning was cancelled before publication")
                self.effects.approved_policy(task_id)
                self.store.add_artifact(
                    task_id=task_id, artifact_id=draft_id, artifact_type="coordinator_plan",
                    media_type="application/json", content=generated.model_dump_json().encode(),
                )
                record = self.plans.save_plan(
                    task_id=task_id, generated_by=PlanOrigin.API_STRONG,
                    sub_questions=tuple(q.question for q in generated.subquestions),
                    source_scope=SourceScope(
                        providers=tuple(generated.sources), year_from=runner.year_from,
                        year_to=generated.retrieval_cutoff.year, min_papers=3,
                        max_papers=limits.max_fulltext_papers,
                    ),
                    exclusions=tuple(generated.exclusion_criteria), details=details,
                    budget_plan=BudgetPlan(
                        max_cny=limits.max_cost_cny, max_api_calls=limits.max_api_calls,
                        max_wall_clock_seconds=limits.max_duration_seconds,
                        estimate_source="approved_runtime_policy",
                    ),
                )
                self.store.add_artifact(
                    task_id=task_id, artifact_id=committed_id, artifact_type="plan_binding",
                    media_type="application/json",
                    content=json.dumps({"plan_version": record.plan_version}).encode(),
                )
            self._record_plan_generated(task_id, record.as_public_dict())
            return record.as_public_dict()
        finally:
            with self._state_lock:
                self._planning_active.pop(task_id, None)

    def _record_plan_generated(self, task_id: str, record: dict[str, Any]) -> None:
        self.ledger.append_event(
            stable_key=f"event:m6:{task_id}:plan-v{record['plan_version']}", task_id=task_id,
            node="approval", kind="plan_generated",
            payload={"plan_version": record["plan_version"], "plan_digest": record["plan_digest"],
                     "generated_by": record["generated_by"]},
        )

    def _run_live_research(self, task_id: str, cancel_event: threading.Event) -> dict[str, Any]:
        runner = self._live_runner
        assert runner is not None
        task = self._task(task_id)
        plan = ResearchPlanView.model_validate(self.current_plan(task_id))
        if task["execution_mode"] != "real" or plan.generated_by == "fixture":
            raise TaskStateError("live research requires an explicitly approved production plan")
        if plan.approval_state != "approved":
            raise TaskStateError("live research plan has not been approved")
        self.effects.require_projection_synced(task_id)
        self.effects.approved_policy(task_id)
        with self._state_lock:
            if cancel_event.is_set():
                return self._finish_cancelled(task_id, reason="cancelled before research")
            self.store.update_task(task_id, status=TaskStatus.RUNNING, phase=TaskPhase.SEARCH)

        def on_event(event: PersistedEvent) -> None:
            if event.task_id != task_id:
                raise TaskStateError("research event belongs to another task")
            # Composition writes directly to the shared durable ledger. The
            # callback only projects the current phase; no late bulk import.
            phases = {"search": TaskPhase.SEARCH, "retrieval": TaskPhase.EVIDENCE,
                      "evidence": TaskPhase.EVIDENCE, "citations": TaskPhase.CITATIONS,
                      "verification": TaskPhase.VERIFICATION, "synthesis": TaskPhase.SYNTHESIS}
            if event.node in phases:
                self.store.update_task(task_id, phase=phases[event.node])

        try:
            result = self.research_result(task_id)
        except TaskStateError:
            result = runner.run(
                question=str(task["question"]), plan=plan, journal=self.effects,
                ledger=self.ledger, cancel_event=cancel_event, on_event=on_event,
            )
        if (result.task_id != task_id or result.plan_version != plan.plan_version
                or result.plan_digest != plan.plan_digest or result.execution_kind != "live"):
            raise TaskStateError("research output does not match the approved production plan")
        with self._state_lock:
            if cancel_event.is_set():
                return self._finish_cancelled(task_id, reason="cancelled before publication")
            self.effects.require_projection_synced(task_id)
            self.store.add_artifact(
                task_id=task_id, artifact_id=f"artifact:m6:{task_id}:research-result",
                artifact_type="research_result", media_type="application/json",
                content=result.model_dump_json().encode(),
            )
            totals = self.effects.summary(task_id)
            status = {"succeeded": TaskStatus.COMPLETED, "degraded": TaskStatus.DEGRADED,
                      "failed": TaskStatus.FAILED}[result.outcome]
            self.store.update_task(
                task_id, status=status, phase=TaskPhase.DONE,
                metrics={"execution_mode": "live", "validation_kind": runner.evidence_kind,
                         "model_calls": totals["committed_calls"] + totals["committed_local_calls"],
                         "provider_calls": totals["committed_external_requests"],
                         "paper_count": len(result.papers), "claim_count": len(result.claims),
                         "evidence_count": len(result.evidence)},
                degradations=result.limitations,
            )
            self.ledger.append_event(
                stable_key=f"event:m6:{task_id}:research-completed", task_id=task_id,
                node="research", kind="workflow_finished",
                payload={"status": status.value, "execution_mode": "live",
                         "validation_kind": runner.evidence_kind},
            )
        return self._write_exports(task_id)

    def _fixture_sub_questions(self, task_id: str) -> tuple[str, ...]:
        """Deterministic sub-questions derived from the task's own question.

        Deterministic by construction so the plan digest is reproducible; this
        is a fixture, not a model-generated decomposition.
        """

        question = str(self._task(task_id)["question"]).strip()
        return (
            f"What does the literature state directly about: {question}",
            f"Which datasets or settings were used to evaluate: {question}",
            f"What limitations or conflicting results are reported for: {question}",
        )

    def _execute_queued(self, task_id: str, cancel_event: threading.Event) -> None:
        try:
            task = self._task(task_id)
            mode = ExecutionMode(str(task.get("execution_mode", ExecutionMode.DEMO.value)))
            # F01: the executor is chosen from the task's declared mode. A real
            # task never silently lands in the demo path.
            executor = select_executor(
                ExecutorMode(mode.value),
                demo=self._demo_executor,
                real=self._real_executor,
            )
            persisted_offline = False
            if mode == ExecutionMode.DEMO and self._offline_research_runner is not None:
                try:
                    self.research_result(task_id)
                    persisted_offline = True
                except TaskStateError:
                    pass
            if not persisted_offline:
                self.budget.check_usage(task_id, self.ledger.usage(task_id))
            executor.run(task_id, cancel_event=cancel_event)
        except (ResearchCancelledError, EffectCancelledError):
            self._finish_cancelled(task_id, reason="research cancelled")
        except (EffectUncertainError, AuthorizationError):
            self.store.update_task(
                task_id,
                status=TaskStatus.INTERRUPTED,
                degradations=["An operation has an uncertain result; it was not repeated."],
            )
            self.ledger.append_event(
                stable_key=f"event:m6:{task_id}:effect-unknown:{uuid.uuid4().hex}",
                task_id=task_id,
                node="recovery",
                kind="task_interrupted",
                payload={"reason": "effect_reconciliation_required"},
            )
        except RealExecutionUnavailableError as exc:
            # Not a failure of the run: the run never started. Recorded as a
            # degradation with the named gaps so the reason is auditable.
            self.store.update_task(
                task_id,
                status=TaskStatus.DEGRADED,
                phase=TaskPhase.DONE,
                metrics={"execution_mode": "real_unavailable", "model_calls": 0},
                degradations=[
                    "Real execution was not attempted: " + ", ".join(gap.reason for gap in exc.gaps)
                ],
            )
            self.ledger.append_event(
                stable_key=f"event:m6:{task_id}:real-unavailable",
                task_id=task_id,
                node="delivery",
                kind="real_execution_unavailable",
                payload=exc.as_public_payload(),
            )
            self._write_exports(task_id)
        except Exception as exc:  # pragma: no cover - exercised by failure injection
            logger.error("task execution failed: %s", type(exc).__name__,
                         extra={"task_id": task_id})
            task = self._task(task_id)
            if TaskStatus(str(task["status"])) not in {
                TaskStatus.CANCELLED,
                TaskStatus.REJECTED,
            }:
                self.store.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    phase=TaskPhase.DONE,
                    degradations=["Task execution failed; inspect the redacted server log."],
                )
                self.ledger.append_event(
                    stable_key=f"event:m6:{task_id}:failed",
                    task_id=task_id,
                    node="queue",
                    kind="task_failed",
                    payload={"error_type": type(exc).__name__},
                )
                self._write_exports(task_id)

    def _run_demo(
        self, task_id: str, *, cancel_event: threading.Event | None = None
    ) -> dict[str, Any]:
        task = self._task(task_id)
        if cancel_event is not None and cancel_event.is_set():
            return self._finish_cancelled(task_id, reason="cancelled before execution")
        mode = DemoMode(str(task["demo_mode"]))
        if mode == DemoMode.PRODUCTION_UNAVAILABLE:
            with self._state_lock:
                if cancel_event is not None and cancel_event.is_set():
                    return self._finish_cancelled(
                        task_id, reason="cancelled before production gate"
                    )
                self.store.update_task(
                    task_id,
                    status=TaskStatus.DEGRADED,
                    phase=TaskPhase.DONE,
                    metrics={"execution_mode": "production_unavailable", "model_calls": 0},
                    degradations=[
                        "api-strong Coordinator/Verifier/Synthesis is disabled; "
                        "no production model call was made."
                    ],
                )
                self.ledger.append_event(
                    stable_key=f"event:m6:{task_id}:degraded",
                    task_id=task_id,
                    node="delivery",
                    kind="workflow_degraded",
                    payload={"reason": "api_strong_disabled"},
                )
            return self._write_exports(task_id)

        phases = [
            (TaskPhase.SEARCH, "search_completed"),
            (TaskPhase.EVIDENCE, "evidence_completed"),
            (TaskPhase.CITATIONS, "citations_completed"),
            (TaskPhase.VERIFICATION, "verification_completed"),
            (TaskPhase.SYNTHESIS, "synthesis_completed"),
        ]
        for phase, event_kind in phases:
            with self._state_lock:
                if cancel_event is not None and cancel_event.is_set():
                    should_cancel = True
                else:
                    should_cancel = False
                    self.store.update_task(task_id, status=TaskStatus.RUNNING, phase=phase)
                    self.ledger.append_event(
                        stable_key=f"event:m6:{task_id}:{phase.value}",
                        task_id=task_id,
                        node=phase.value,
                        kind=event_kind,
                        payload={"execution_mode": "deterministic_delivery_demo"},
                    )
            if should_cancel:
                return self._finish_cancelled(task_id, reason=f"cancelled before {phase.value}")
        degradations = []
        status = TaskStatus.COMPLETED
        if mode == DemoMode.DEGRADED:
            status = TaskStatus.DEGRADED
            degradations = [
                "Demo injected a bounded ScholarGraph fallback; the core evidence "
                "path remained available.",
                "This deterministic demo does not claim semantic quality or "
                "production model capacity.",
            ]
            self.ledger.append_event(
                stable_key=f"event:m6:{task_id}:fallback",
                task_id=task_id,
                node="scholargraph",
                kind="fallback_to_b3",
                payload={"reason": "demo_injected_degradation"},
            )
        metrics = {
            "execution_mode": "deterministic_delivery_demo",
            "model_calls": 0,
            "provider_calls": 0,
            "evidence_count": 3,
            "citation_edge_count": 1,
            "verification_states": ["supported", "partially_supported", "conflicted"],
            "export_formats": ["json", "markdown", "html", "pdf"],
        }
        with self._state_lock:
            if cancel_event is not None and cancel_event.is_set():
                should_cancel = True
            else:
                should_cancel = False
                self.store.update_task(
                    task_id,
                    status=status,
                    phase=TaskPhase.DONE,
                    metrics=metrics,
                    degradations=degradations,
                )
                self.ledger.append_event(
                    stable_key=f"event:m6:{task_id}:finished",
                    task_id=task_id,
                    node="delivery",
                    kind="workflow_finished",
                    payload={"status": status.value},
                )
        if should_cancel:
            return self._finish_cancelled(task_id, reason="cancelled before synthesis completed")
        return self._write_exports(task_id)

    def _finish_cancelled(self, task_id: str, *, reason: str) -> dict[str, Any]:
        with self._state_lock:
            task = self._task(task_id)
            if TaskStatus(str(task["status"])) != TaskStatus.CANCELLED:
                degradations = list(task["degradations"])
                if reason not in degradations:
                    degradations.append(reason)
                self.store.update_task(
                    task_id,
                    status=TaskStatus.CANCELLED,
                    phase=TaskPhase.DONE,
                    degradations=degradations,
                )
                self.ledger.append_event(
                    stable_key=f"event:m6:{task_id}:cancelled",
                    task_id=task_id,
                    node="queue",
                    kind="task_cancelled",
                    payload={"reason": reason},
                )
        return self._write_exports(task_id)

    def _prepare_execution_authorization(
        self, task_id: str, request: ApprovalRequest,
    ) -> PhaseGrant:
        approved = request.execution_authorization
        if approved is None:
            raise AuthorizationError("real execution requires explicit phase authorization")
        policy = self.effects.approved_policy(task_id)
        if (self._runtime_policy is None or self._runtime_policy.digest() != policy.digest()
                or approved.policy_sha256 != policy.digest()):
            raise AuthorizationError("execution policy differs from the original task policy")
        plan = self.plans.current_plan(task_id)
        if (Decimal(approved.max_cny) > Decimal(str(plan.budget_plan.max_cny))
                or approved.max_remote_calls > plan.budget_plan.max_api_calls):
            raise AuthorizationError("execution allowance exceeds the reviewed plan")
        if plan.source_scope.max_papers > policy.max_papers:
            raise AuthorizationError("plan paper count exceeds the approved task policy")
        if not set(plan.source_scope.providers).issubset(policy.allowed_search_providers):
            raise AuthorizationError("plan providers exceed the approved task policy")
        totals = self.effects.summary(task_id)
        if totals["uncertain_effects"]:
            raise AuthorizationError("task has unresolved operations before execution approval")
        phase = PhaseGrant(
            task_id=task_id, authorization_id=f"execution:{approved.generation}", phase="execution",
            plan_version=plan.plan_version, plan_digest=plan.plan_digest,
            acknowledgement_sha256=hashlib.sha256(request.model_dump_json().encode()).hexdigest(),
            **approved.model_dump(),
        )
        self.effects.prepare_phase(phase)
        maximum, calls = self.effects.approved_limits(task_id)
        reservation = self.budget.get(task_id)
        if reservation is None:
            raise AuthorizationError("original task capacity is missing")
        self.budget.bind_authorized_plan(
            task_id=task_id, policy_sha256=policy.digest(), plan_version=plan.plan_version,
            plan_digest=plan.plan_digest, expected_cny=maximum, expected_api_calls=calls,
            expected_wall_clock_seconds=reservation.reserved_wall_clock_seconds,
        )
        return phase

    def _reserve_for_task(self, task_id: str) -> None:
        """Reserve the approved plan's budget for a whole task (T07).

        Task limits come from the reviewed plan. An optional explicitly configured
        capacity is shared across open reservations, not a cumulative spending cap.
        Existing reservations are conflicts, never silently reused.
        """

        reserved_cny = 0.0
        reserved_calls = 0
        reserved_seconds = 0
        plan_version = 0
        plan_digest = "no-plan"
        source = "demo_no_paid_calls"
        try:
            plan = self.plans.current_plan(task_id)
        except PlanNotFoundError:
            plan = None
        if plan is not None:
            budget = plan.budget_plan
            reserved_cny = float(budget.max_cny)
            reserved_calls = int(budget.max_api_calls)
            reserved_seconds = int(budget.max_wall_clock_seconds)
            plan_version = int(plan.plan_version)
            plan_digest = str(plan.plan_digest)
            source = str(budget.estimate_source)
        self.budget.reserve(
            task_id=task_id,
            plan_version=plan_version,
            plan_digest=plan_digest,
            reserved_cny=reserved_cny,
            reserved_api_calls=reserved_calls,
            reserved_wall_clock_seconds=reserved_seconds,
            estimate_source=source,
            ceiling_cny=self._reservation_capacity_cny,
        )

    def _record_reservation_event(self, task_id: str) -> None:
        reservation = self.budget.get(task_id)
        assert reservation is not None
        reserved_cny = reservation.reserved_cny
        reserved_calls = reservation.reserved_api_calls
        source = reservation.estimate_source
        self.ledger.append_event(
            stable_key=f"event:m6:{task_id}:budget-reserved",
            task_id=task_id,
            node="budget",
            kind="budget_reserved",
            payload={
                "reserved_cny": reserved_cny,
                "reserved_api_calls": reserved_calls,
                "estimate_source": source,
                # Never a bill: this is a ceiling set aside, not money spent.
                "is_actual_bill": False,
            },
        )

    def _settle_for_task(self, task_id: str, *, reason: str) -> None:
        """Close the reservation at a terminal state (T07).

        Settles from recorded ledger usage, not from the reservation, so the
        number reflects what was measured. `is_actual_bill` stays false: this is
        a local accounting record, and only reconciliation against a provider
        invoice may claim otherwise.

        Never raises: a bookkeeping failure must not prevent a task from
        reaching a terminal state, or the task would hang and the reservation
        would leak anyway. An unsettled row stays visible to `reconcile_orphans`.
        """

        try:
            if self.budget.get(task_id) is None:
                return
            usage = self.ledger.usage(task_id)
            if self._task(task_id)["execution_mode"] == "real":
                self.budget.mark_unconfirmed(task_id, usage)
                return
            settled = self.budget.settle(
                task_id=task_id,
                settled_cny=float(usage.external_cost_cny),
                settled_api_calls=int(usage.api_calls),
                reason=reason,
                is_actual_bill=False,
            )
            self.ledger.append_event(
                stable_key=f"event:m6:{task_id}:budget-settled",
                task_id=task_id,
                node="budget",
                kind="budget_settled",
                payload={
                    "settled_cny": settled.settled_cny,
                    "settled_api_calls": settled.settled_api_calls,
                    "reserved_cny": settled.reserved_cny,
                    "is_actual_bill": False,
                },
            )
        except Exception:  # noqa: BLE001 - terminal transition must not depend on bookkeeping
            logger.exception("budget settlement failed for %s", task_id)

    def budget_report(self, task_id: str) -> dict[str, Any]:
        """Reserved vs settled for one task, with the estimate/bill distinction."""

        self._task(task_id)
        reservation = self.budget.get(task_id)
        if self.effects.has_authorization(task_id):
            projection = self.effects.project_usage(task_id, self.ledger)
            return {
                "schema_version": "1.0", "task_id": task_id,
                "reservation": None if reservation is None else reservation.as_public_dict(),
                "measured_usage": self.ledger.usage(task_id).model_dump(mode="json")
                if projection["state"] == "synced" else None,
                "journal_accounting": projection["authoritative"],
                "projection_state": projection["state"], "is_actual_bill": False,
                "committed_cny_all_tasks": self.budget.committed_cny(),
                "note": "Journal usage is a local reference estimate, not a provider bill.",
            }
        if reservation is None:
            return {
                "schema_version": "1.0",
                "task_id": task_id,
                "reservation": None,
                "measured_usage": self.ledger.usage(task_id).model_dump(mode="json"),
                "is_actual_bill": False,
                "note": "No budget reservation exists for this task.",
            }
        return {
            "schema_version": "1.0",
            "task_id": task_id,
            "reservation": reservation.as_public_dict(),
            "measured_usage": self.ledger.usage(task_id).model_dump(mode="json"),
            "is_actual_bill": reservation.is_actual_bill,
            "committed_cny_all_tasks": self.budget.committed_cny(),
        }

    def retry_exports(self, task_id: str) -> dict[str, Any]:
        """Retry only terminal report rendering; never rerun research effects."""

        task = self._task(task_id)
        if task["status"] not in {
            TaskStatus.COMPLETED.value,
            TaskStatus.DEGRADED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.REJECTED.value,
        }:
            raise TaskStateError("exports can only be retried after a terminal task state")
        return self._write_exports(task_id)

    def _write_exports(self, task_id: str) -> dict[str, Any]:
        """Write terminal exports, guaranteeing a terminal event either way (T15).

        Every terminal path funnels through here, and `exports_ready` is what
        closes the SSE stream. If export rendering raises, an unguarded failure
        would emit no terminal event at all: the stream would stay open, the
        client would exhaust its retries, and the task would display as running
        forever despite being finished server-side.

        So a failure still emits a terminal event — `task_terminal_no_exports` —
        which says what actually happened rather than claiming exports exist,
        and a task whose status said `completed` is downgraded to `degraded`:
        reporting a run as completed when its report does not exist would
        overstate what was delivered.
        """

        # T07: settle BEFORE writing exports. `exports_ready` closes the SSE
        # stream, so any event emitted after it never reaches the client — and
        # settling first also means the report renders with the final figures.
        self._settle_for_task(task_id, reason="terminal state reached")
        try:
            return self._write_exports_unguarded(task_id)
        except Exception as exc:  # noqa: BLE001 - terminal closure must not depend on the cause
            logger.exception("export rendering failed for %s", task_id)
            self.ledger.append_event(
                stable_key=f"event:m6:{task_id}:terminal-no-exports",
                task_id=task_id,
                node="delivery",
                kind="task_terminal_no_exports",
                payload={"error_type": type(exc).__name__},
            )
            summary = self.summary(task_id)
            existing = list(summary.get("degradations") or [])
            note = "Terminal state reached but exports could not be rendered."
            updates: dict[str, Any] = {}
            if note not in existing:
                updates["degradations"] = [*existing, note]
            # Only downgrade a success claim. failed/cancelled/rejected already
            # describe the outcome accurately and must not be overwritten.
            if str(summary.get("status")) == TaskStatus.COMPLETED.value:
                updates["status"] = TaskStatus.DEGRADED
            if updates:
                self.store.update_task(task_id, **updates)
                summary = self.summary(task_id)
            return summary

    def _write_exports_unguarded(self, task_id: str) -> dict[str, Any]:
        task = self._task(task_id)
        events = self.events(task_id)
        existing = self.store.list_artifacts(task_id)
        expected = {
            f"artifact:m6:{task_id}:report:{fmt}" for fmt in ("json", "markdown", "html", "pdf")
        }
        if expected <= {item["artifact_id"] for item in existing}:
            self.ledger.append_event(
                stable_key=f"event:m6:{task_id}:exports",
                task_id=task_id,
                node="delivery",
                kind="exports_ready",
                payload={"formats": ["json", "markdown", "html", "pdf"]},
            )
            return self.summary(task_id)
        markdown = render_markdown(task=task, events=events, artifacts=existing)
        html_text = render_html(task=task, events=events, artifacts=existing)
        research = None
        with suppress(TaskStateError):
            research = self.research_result(task_id)
        if research is not None:
            markdown = research.report_markdown
            html_text = (
                '<!doctype html><meta charset="utf-8"><pre>' + html.escape(markdown) + "</pre>"
            )
        report_json = json.dumps(
            {
                "schema_version": "1.0",
                "task": {key: value for key, value in task.items() if key not in {"question"}},
                "events": [
                    {"event_id": item["event_id"], "kind": item["kind"], "node": item["node"]}
                    for item in events
                ],
                "limitations": [
                    "No API keys, prompts, raw model answers, or paper full text are included."
                ],
                **({"research": research.model_dump(mode="json")} if research is not None else {}),
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode("utf-8")
        exports = (
            ("report:json", "report", "application/json", report_json),
            ("report:markdown", "report", "text/markdown; charset=utf-8", markdown.encode("utf-8")),
            ("report:html", "report", "text/html; charset=utf-8", html_text.encode("utf-8")),
            ("report:pdf", "report", "application/pdf", render_pdf(markdown=markdown)),
        )
        for suffix, artifact_type, media_type, content in exports:
            self.store.add_artifact(
                task_id=task_id,
                artifact_id=f"artifact:m6:{task_id}:{suffix}",
                artifact_type=artifact_type,
                media_type=media_type,
                content=content,
            )
        self.ledger.append_event(
            stable_key=f"event:m6:{task_id}:exports",
            task_id=task_id,
            node="delivery",
            kind="exports_ready",
            payload={"formats": ["json", "markdown", "html", "pdf"]},
        )
        return self.summary(task_id)

    def _task(self, task_id: str) -> dict[str, Any]:
        try:
            return self.store.get_task(task_id)
        except KeyError as exc:
            raise TaskNotFoundError(task_id) from exc
