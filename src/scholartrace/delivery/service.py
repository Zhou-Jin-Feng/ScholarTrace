"""M6 task service built around durable metadata and the existing event ledger."""

from __future__ import annotations

import hashlib
import json
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
from scholartrace.delivery.reporting import render_html, render_markdown, render_pdf
from scholartrace.delivery.store import DeliveryStore
from scholartrace.workflow.storage import RuntimeLedger


class TaskNotFoundError(KeyError):
    """The requested task or artifact does not exist."""


class TaskStateError(ValueError):
    """The requested task transition is not allowed."""


class M6TaskService:
    def __init__(self, *, root: Path, data_dir: Path | None = None) -> None:
        self.root = root
        storage_root = data_dir or root / "artifacts" / "m6-delivery"
        storage_root.mkdir(parents=True, exist_ok=True)
        self.store = DeliveryStore(storage_root / "tasks.sqlite")
        self.ledger = RuntimeLedger(storage_root / "runtime.sqlite")

    def close(self) -> None:
        self.ledger.close()
        self.store.close()

    def create_task(
        self,
        request: TaskCreateRequest,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        request_hash = hashlib.sha256(
            request.model_dump_json().encode("utf-8")
        ).hexdigest()
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
        return self._run_demo(task_id)

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

    def evaluation_matrix(self) -> dict[str, Any]:
        return build_m6_evaluation_matrix(root=self.root).model_dump(mode="json")

    def _run_demo(self, task_id: str) -> dict[str, Any]:
        task = self._task(task_id)
        mode = DemoMode(str(task["demo_mode"]))
        if mode == DemoMode.PRODUCTION_UNAVAILABLE:
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
            self.store.update_task(task_id, status=TaskStatus.RUNNING, phase=phase)
            self.ledger.append_event(
                stable_key=f"event:m6:{task_id}:{phase.value}",
                task_id=task_id,
                node=phase.value,
                kind=event_kind,
                payload={"execution_mode": "deterministic_delivery_demo"},
            )
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
                "task": {
                    key: value
                    for key, value in task.items()
                    if key not in {"question"}
                },
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
