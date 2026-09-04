"""M6 task service built around durable metadata and the existing event ledger."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from scholartrace.delivery.evaluation import build_m6_evaluation_matrix
from scholartrace.delivery.models import (
    ApprovalRequest,
    DemoMode,
    TaskCreateRequest,
    TaskPhase,
    TaskStatus,
)
from scholartrace.delivery.queue import (
    BoundedTaskExecutor,
    QueueClosedError,
    QueueFullError,
)
from scholartrace.delivery.reporting import render_html, render_markdown, render_pdf
from scholartrace.delivery.store import DeliveryStore
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


class M6TaskService:
    def __init__(
        self,
        *,
        root: Path,
        data_dir: Path | None = None,
        queue_capacity: int | None = None,
        worker_count: int | None = None,
        completion_wait_seconds: float = 0.5,
    ) -> None:
        self.root = root
        storage_root = data_dir or root / "artifacts" / "m6-delivery"
        storage_root.mkdir(parents=True, exist_ok=True)
        self.store = DeliveryStore(storage_root / "tasks.sqlite")
        self.ledger = RuntimeLedger(storage_root / "runtime.sqlite")
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

    def close(self) -> None:
        if self._closed:
            return
        drained = self._executor.shutdown(timeout_seconds=5.0)
        if not drained:
            logger.warning("task executor did not drain before shutdown timeout")
            return
        self._closed = True
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
        task = self._task(task_id)
        if task["status"] != TaskStatus.WAITING_APPROVAL.value:
            raise TaskStateError("only waiting_approval tasks can be approved")
        if request.action == "reject":
            self.store.update_task(
                task_id,
                status=TaskStatus.REJECTED,
                phase=TaskPhase.DONE,
                degradations=[request.reason or "plan rejected by user"],
            )
            self.ledger.append_event(
                stable_key=f"event:m6:{task_id}:rejected",
                task_id=task_id,
                node="approval",
                kind="plan_rejected",
                payload={"reason": request.reason or "not_provided"},
            )
            self._write_exports(task_id)
            return self.summary(task_id)

        if request.action == "modify":
            self.ledger.append_event(
                stable_key=f"event:m6:{task_id}:modified",
                task_id=task_id,
                node="approval",
                kind="plan_modified",
                payload={"reason": request.reason or "modified_by_user"},
            )
        else:
            self.ledger.append_event(
                stable_key=f"event:m6:{task_id}:approved",
                task_id=task_id,
                node="approval",
                kind="plan_approved",
            )
        self.store.update_task(task_id, status=TaskStatus.QUEUED, phase=TaskPhase.PLAN)
        try:

            def execute_queued(cancel_event: threading.Event) -> None:
                self._execute_queued(task_id, cancel_event)

            submission = self._executor.submit(
                task_id,
                execute_queued,
            )
        except QueueFullError as exc:
            self.store.update_task(
                task_id,
                status=TaskStatus.WAITING_APPROVAL,
                phase=TaskPhase.PLAN,
                degradations=["Local task queue is full; approval can be retried."],
            )
            self.ledger.append_event(
                stable_key=f"event:m6:{task_id}:queue-rejected",
                task_id=task_id,
                node="queue",
                kind="queue_rejected",
                payload={"reason": "queue_full"},
            )
            raise TaskQueueFullError("task queue is full") from exc
        except QueueClosedError as exc:
            raise TaskQueueClosedError("task queue is shutting down") from exc
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
            if status == TaskStatus.WAITING_APPROVAL:
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

    def list_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        self._task(task_id)
        return self.store.list_artifacts(task_id)

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

    def _execute_queued(self, task_id: str, cancel_event: threading.Event) -> None:
        try:
            self._run_demo(task_id, cancel_event=cancel_event)
        except Exception as exc:  # pragma: no cover - exercised by failure injection
            logger.exception("task execution failed", extra={"task_id": task_id})
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

    def _write_exports(self, task_id: str) -> dict[str, Any]:
        task = self._task(task_id)
        events = self.events(task_id)
        existing = self.store.list_artifacts(task_id)
        markdown = render_markdown(task=task, events=events, artifacts=existing)
        html_text = render_html(task=task, events=events, artifacts=existing)
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
