"""Runnable FastAPI application for the M6 research workspace."""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from scholartrace import __version__
from scholartrace.api.events import create_event_router
from scholartrace.api.security import SecurityPolicy
from scholartrace.delivery.authorization import AuthorizationError, RuntimePolicy
from scholartrace.delivery.budget import BudgetError
from scholartrace.delivery.models import (
    ApprovalRequest,
    ArtifactSummary,
    BudgetReport,
    EvaluationMatrix,
    PlanCostAcknowledgement,
    ResearchPlanView,
    ResumeRequest,
    TaskCreateRequest,
    TaskStatus,
    TaskSummary,
)
from scholartrace.delivery.plans import PlanError, PlanNotFoundError
from scholartrace.delivery.research import OfflineResearchRunner, ResearchResult
from scholartrace.delivery.service import (
    LiveResearchRunner,
    M6TaskService,
    PlanVersionRequiredError,
    RealExecutionUnavailable,
    TaskNotFoundError,
    TaskQueueClosedError,
    TaskQueueFullError,
    TaskStateError,
)
from scholartrace.delivery.store import InvalidCursorError
from scholartrace.delivery.views import ClaimsResponse, EvidenceView

logger = logging.getLogger("scholartrace.delivery")


def create_app(
    *,
    root: Path | None = None,
    data_dir: Path | None = None,
    queue_capacity: int | None = None,
    worker_count: int | None = None,
    completion_wait_seconds: float = 0.5,
    deployment_mode: str | None = None,
    auth_token: str | None = None,
    offline_research_runner: OfflineResearchRunner | None = None,
    runtime_policy: RuntimePolicy | None = None,
    live_runner: LiveResearchRunner | None = None,
) -> FastAPI:
    project_root = root or Path(__file__).resolve().parents[3]
    configured_data_dir = data_dir
    if configured_data_dir is None:
        environment_data_dir = os.environ.get("SCHOLARTRACE_DATA_DIR")
        if environment_data_dir:
            configured_data_dir = Path(environment_data_dir)
    security = SecurityPolicy.from_environment(
        deployment_mode=deployment_mode,
        auth_token=auth_token,
    )
    service = M6TaskService(
        root=project_root,
        data_dir=configured_data_dir,
        queue_capacity=queue_capacity,
        worker_count=worker_count,
        completion_wait_seconds=completion_wait_seconds,
        offline_research_runner=offline_research_runner,
        runtime_policy=runtime_policy,
        live_runner=live_runner,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            service.close()

    app = FastAPI(
        title="ScholarTrace Research Workspace",
        version=__version__,
        description="Evidence-grounded research task delivery API",
        lifespan=lifespan,
    )
    app.state.m6_service = service
    app.state.security_policy = security

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        if security.allows(request):
            response = await call_next(request)
        else:
            response = JSONResponse(
                status_code=401,
                content={"detail": "authentication required"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        response.headers["X-Request-ID"] = request_id
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "request_id": request_id,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return response

    api = APIRouter(prefix="/api/v1", tags=["research-workspace"])

    @api.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "live", "service": "scholartrace", "version": __version__}

    @api.get("/health/ready")
    def ready() -> dict[str, object]:
        snapshot = service.queue_snapshot()
        return {
            "status": "ready" if snapshot["accepting"] else "draining",
            "service": "scholartrace",
            "deployment_mode": security.mode.value,
            "auth_required": security.auth_required,
            "queue": snapshot,
        }

    @api.get("/health/dependencies")
    def dependencies() -> dict[str, object]:
        """T16: read-only readiness. No model load, no provider call, no secrets.

        Replaces the frontend's hardcoded "API ready": `/health/ready` only ever
        reflected the local queue, so a workspace with no DocuMind, no local
        model and a closed paid gate still displayed as ready.
        """

        return service.dependency_report()

    @api.post("/health/dependencies/probe")
    async def probe_dependencies() -> dict[str, object]:
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
                return await service.probe_dependencies(client)
        except (AuthorizationError, TaskStateError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.get("/evaluation/m6", response_model=EvaluationMatrix)
    def evaluation() -> EvaluationMatrix:
        return EvaluationMatrix.model_validate(service.evaluation_matrix())

    @api.post("/research/tasks", response_model=TaskSummary, status_code=201)
    def create_task(
        payload: TaskCreateRequest,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> TaskSummary:
        try:
            return TaskSummary.model_validate(
                service.create_task(payload, idempotency_key=idempotency_key)
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.get("/research/tasks")
    def list_tasks(
        status: TaskStatus | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        """Keyset-paginated task history (T13).

        Registered before the `{task_id}` route so the collection path is not
        captured by the path parameter.
        """
        try:
            page = service.list_tasks(status=status, cursor=cursor, limit=limit)
        except InvalidCursorError as exc:
            raise HTTPException(
                status_code=400, detail={"code": "invalid_cursor", "detail": str(exc)}
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"schema_version": "1.0", **page}

    @api.get("/research/tasks/{task_id}", response_model=TaskSummary)
    def get_task(task_id: str) -> TaskSummary:
        try:
            return TaskSummary.model_validate(service.summary(task_id))
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @api.get("/research/tasks/{task_id}/plan", response_model=ResearchPlanView)
    def get_plan(task_id: str) -> dict[str, object]:
        try:
            return service.current_plan(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except PlanNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail={"code": "plan_not_found", "detail": str(exc)}
            ) from exc

    @api.get("/research/tasks/{task_id}/plan/versions")
    def get_plan_versions(task_id: str) -> dict[str, object]:
        try:
            return service.plan_versions(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @api.post("/research/tasks/{task_id}/plan/estimate")
    def estimate_plan(task_id: str) -> dict[str, object]:
        """Gate A: what producing a plan would cost, before producing one."""
        try:
            return service.estimate_plan(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @api.post("/research/tasks/{task_id}/plan/acknowledge-cost")
    def acknowledge_plan_cost(task_id: str, payload: PlanCostAcknowledgement) -> dict[str, object]:
        try:
            return service.acknowledge_plan_cost(
                task_id, acknowledged_max_cny=payload.acknowledged_max_cny,
                authorization=payload.authorization,
            )
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except PlanError as exc:
            raise HTTPException(
                status_code=422, detail={"code": exc.code, "detail": str(exc)}
            ) from exc
        except (AuthorizationError, BudgetError, TaskStateError) as exc:
            raise HTTPException(
                status_code=409, detail={"code": "authorization_refused", "detail": str(exc)}
            ) from exc

    @api.post("/research/tasks/{task_id}/plan/generate", response_model=ResearchPlanView)
    def generate_plan(task_id: str) -> dict[str, object]:
        try:
            return service.generate_plan(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except RealExecutionUnavailable as exc:
            raise HTTPException(status_code=503, detail=exc.payload) from exc
        except PlanError as exc:
            raise HTTPException(
                status_code=409, detail={"code": exc.code, "detail": str(exc)}
            ) from exc

        except (TaskStateError, AuthorizationError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.get(
        "/research/tasks/{task_id}/budget",
        response_model=BudgetReport,
        response_model_exclude_unset=True,
    )
    def task_budget(task_id: str) -> dict[str, object]:
        """T07: reserved vs settled, with the estimate/bill distinction explicit."""

        try:
            return service.budget_report(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @api.post("/research/tasks/{task_id}/approve", response_model=TaskSummary)
    def approve_task(task_id: str, payload: ApprovalRequest) -> TaskSummary:
        try:
            return TaskSummary.model_validate(service.approve_task(task_id, payload))
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except AuthorizationError as exc:
            raise HTTPException(
                status_code=409, detail={"code": "authorization_refused", "detail": str(exc)}
            ) from exc
        except TaskQueueFullError as exc:
            raise HTTPException(
                status_code=429,
                detail=str(exc),
                headers={"Retry-After": "1"},
            ) from exc
        except TaskQueueClosedError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except PlanVersionRequiredError as exc:
            # F02: refuse a decision that does not name the plan it applies to.
            raise HTTPException(
                status_code=409, detail={"code": exc.code, "detail": str(exc)}
            ) from exc
        except PlanError as exc:
            # Covers PlanVersionStaleError and an already-decided plan: the
            # reviewer read a superseded revision and must re-read before deciding.
            raise HTTPException(
                status_code=409, detail={"code": exc.code, "detail": str(exc)}
            ) from exc
        except BudgetError as exc:
            raise HTTPException(
                status_code=409, detail={"code": exc.code, "detail": str(exc)}
            ) from exc
        except TaskStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.post("/research/tasks/{task_id}/cancel", response_model=TaskSummary)
    def cancel_task(task_id: str) -> TaskSummary:
        try:
            return TaskSummary.model_validate(service.cancel_task(task_id))
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except BudgetError as exc:
            raise HTTPException(
                status_code=409, detail={"code": exc.code, "detail": str(exc)}
            ) from exc
        except TaskStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.post("/research/tasks/{task_id}/resume", response_model=TaskSummary)
    def resume_task(task_id: str, payload: ResumeRequest) -> dict[str, object]:
        try:
            return service.resume_task(
                task_id, plan_version=payload.plan_version, plan_digest=payload.plan_digest
            )
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except AuthorizationError as exc:
            raise HTTPException(
                status_code=409, detail={"code": "authorization_refused", "detail": str(exc)}
            ) from exc
        except TaskQueueFullError as exc:
            raise HTTPException(
                status_code=429, detail=str(exc), headers={"Retry-After": "1"}
            ) from exc
        except TaskQueueClosedError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except TaskStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.get("/research/tasks/{task_id}/artifacts", response_model=list[ArtifactSummary])
    def list_artifacts(task_id: str) -> list[ArtifactSummary]:
        try:
            return [
                ArtifactSummary.model_validate(item) for item in service.list_artifacts(task_id)
            ]
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @api.get("/research/tasks/{task_id}/research-result", response_model=ResearchResult)
    def research_result(task_id: str) -> ResearchResult:
        try:
            return service.research_result(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except TaskStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.get("/research/tasks/{task_id}/claims", response_model=ClaimsResponse)
    def claims(task_id: str) -> dict[str, object]:
        try:
            return service.claims(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except TaskStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.get("/research/tasks/{task_id}/evidence/{evidence_id:path}", response_model=EvidenceView)
    def evidence(task_id: str, evidence_id: str) -> dict[str, object]:
        try:
            return service.evidence(task_id, evidence_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="evidence not found") from exc

    @api.get("/research/tasks/{task_id}/timeline")
    def timeline(task_id: str) -> dict[str, object]:
        try:
            return {"schema_version": "1.0", "task_id": task_id, "events": service.events(task_id)}
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @api.get("/research/tasks/{task_id}/report")
    def report(
        task_id: str,
        format: Literal["json", "markdown", "html", "pdf"] = "markdown",
    ) -> Response:
        suffix = f"report:{format}"
        try:
            artifacts = service.list_artifacts(task_id)
            artifact_id = next(
                item["artifact_id"] for item in artifacts if item["artifact_id"].endswith(suffix)
            )
            artifact = service.artifact(task_id, artifact_id)
        except (TaskNotFoundError, StopIteration) as exc:
            raise HTTPException(status_code=404, detail="report not found") from exc
        extension = "md" if format == "markdown" else format
        return Response(
            content=artifact["content"],
            media_type=artifact["media_type"].split(";", maxsplit=1)[0],
            headers={
                "Content-Disposition": (
                    f'attachment; filename="scholartrace-{format}.{extension}"'
                ),
                "X-Content-SHA256": artifact["content_sha256"],
            },
        )

    @api.post("/research/tasks/{task_id}/report/retry", response_model=TaskSummary)
    def retry_report(task_id: str) -> dict[str, object]:
        try:
            return service.retry_exports(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except TaskStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    app.include_router(api)
    app.include_router(create_event_router(service.ledger, task_exists=service.task_exists))
    return app

