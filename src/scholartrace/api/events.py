"""Read-only SSE replay endpoint for durable M3 workflow events."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import StreamingResponse

from scholartrace.workflow.events import replay_sse
from scholartrace.workflow.storage import RuntimeLedger


def create_event_router(ledger: RuntimeLedger) -> APIRouter:
    router = APIRouter(prefix="/api/v1/research/tasks", tags=["workflow-events"])

    @router.get("/{task_id}/events", response_class=StreamingResponse)
    def events(
        task_id: str,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        try:
            payloads = replay_sse(
                ledger,
                task_id=task_id,
                last_event_id=last_event_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Last-Event-ID") from exc

        def generate() -> Iterator[str]:
            yield from payloads

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
