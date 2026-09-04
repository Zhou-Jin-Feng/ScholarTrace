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

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from scholartrace.api.events import create_event_router
from scholartrace.api.security import SecurityPolicy
from scholartrace.delivery.models import (
    ApprovalRequest,
    ArtifactSummary,
    EvaluationMatrix,
    TaskCreateRequest,
    TaskSummary,
)
from scholartrace.delivery.service import (
    M6TaskService,
    TaskNotFoundError,
    TaskQueueClosedError,
    TaskQueueFullError,
    TaskStateError,
)

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
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            service.close()

    app = FastAPI(
        title="ScholarTrace Research Workspace",
        version="0.5.0",
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
        return {"status": "live", "service": "scholartrace", "version": "0.5.0"}

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

    @api.get("/research/tasks/{task_id}", response_model=TaskSummary)
    def get_task(task_id: str) -> TaskSummary:
        try:
            return TaskSummary.model_validate(service.summary(task_id))
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    @api.post("/research/tasks/{task_id}/approve", response_model=TaskSummary)
    def approve_task(task_id: str, payload: ApprovalRequest) -> TaskSummary:
        try:
            return TaskSummary.model_validate(service.approve_task(task_id, payload))
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
        except TaskQueueFullError as exc:
            raise HTTPException(
                status_code=429,
                detail=str(exc),
                headers={"Retry-After": "1"},
            ) from exc
        except TaskQueueClosedError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except TaskStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.post("/research/tasks/{task_id}/cancel", response_model=TaskSummary)
    def cancel_task(task_id: str) -> TaskSummary:
        try:
            return TaskSummary.model_validate(service.cancel_task(task_id))
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc
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

    app.include_router(api)
    app.include_router(create_event_router(service.ledger, task_exists=service.task_exists))
    return app


app = create_app()
