"""Replay and follow durable workflow events over Server-Sent Events."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from scholartrace.workflow.events import encode_sse, replay_sse
from scholartrace.workflow.models import PersistedEvent
from scholartrace.workflow.storage import RuntimeLedger

SSE_POLL_SECONDS = 0.25
SSE_HEARTBEAT_SECONDS = 15.0
SSE_RETRY_MILLISECONDS = 1000
#: Kinds that close the stream (T15).
#:
#: `exports_ready` is the normal last event on every terminal path — it is
#: emitted *after* task_failed / task_cancelled / plan_rejected, so those must
#: NOT close the stream or the client would never receive its own exports.
#:
#: `task_terminal_no_exports` is the failure counterpart: when export rendering
#: raises, it is the last event instead. Without it here, a task that finished
#: but could not render would hold the stream open until the client gave up and
#: then display as running forever.
SSE_TERMINAL_EVENT_KINDS = frozenset({"exports_ready", "task_terminal_no_exports"})


def _control_event(name: str, payload: dict[str, object]) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"event: {name}\ndata: {data}\n\n"


async def follow_sse(
    ledger: RuntimeLedger,
    *,
    task_id: str,
    initial_events: list[PersistedEvent],
    last_event_id: str | None,
    request: Request,
    poll_seconds: float,
    heartbeat_seconds: float,
    retry_milliseconds: int,
    terminal_event_kinds: frozenset[str],
) -> AsyncIterator[str]:
    """Yield existing and newly persisted events until disconnect or terminal drain."""

    yield f"retry: {retry_milliseconds}\n\n"
    cursor = last_event_id
    pending = initial_events
    loop = asyncio.get_running_loop()
    last_write = loop.time()
    terminal_seen = bool(
        cursor and ledger.event_kind(task_id=task_id, event_id=cursor) in terminal_event_kinds
    )

    while True:
        if terminal_seen:
            yield _control_event("stream_end", {"reason": "terminal"})
            return
        if pending:
            for event in pending:
                yield encode_sse(event)
                cursor = event.event_id
                last_write = loop.time()
            if any(event.kind in terminal_event_kinds for event in pending):
                terminal_seen = True
                yield _control_event("stream_end", {"reason": "terminal"})
                return

        if await request.is_disconnected():
            return

        now = loop.time()
        if now - last_write >= heartbeat_seconds:
            yield ": heartbeat\n\n"
            last_write = now

        await asyncio.sleep(poll_seconds)
        pending = ledger.replay(task_id=task_id, last_event_id=cursor)


def create_event_router(
    ledger: RuntimeLedger,
    *,
    poll_seconds: float = SSE_POLL_SECONDS,
    heartbeat_seconds: float = SSE_HEARTBEAT_SECONDS,
    retry_milliseconds: int = SSE_RETRY_MILLISECONDS,
    terminal_event_kinds: frozenset[str] = SSE_TERMINAL_EVENT_KINDS,
    task_exists: Callable[[str], bool] | None = None,
) -> APIRouter:
    if poll_seconds <= 0 or heartbeat_seconds <= 0 or retry_milliseconds <= 0:
        raise ValueError("SSE timing values must be positive")
    router = APIRouter(prefix="/api/v1/research/tasks", tags=["workflow-events"])

    @router.get("/{task_id}/events", response_class=StreamingResponse)
    async def events(
        request: Request,
        task_id: str,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
        cursor: Annotated[str | None, Query(alias="last_event_id")] = None,
        follow: bool = False,
    ) -> StreamingResponse:
        if task_exists is not None and not task_exists(task_id):
            raise HTTPException(status_code=404, detail="task not found")
        if last_event_id is not None and cursor is not None and last_event_id != cursor:
            raise HTTPException(status_code=400, detail="conflicting Last-Event-ID")
        resolved_cursor = last_event_id or cursor
        try:
            initial_events = ledger.replay(task_id=task_id, last_event_id=resolved_cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Last-Event-ID") from exc

        if follow:
            payloads: AsyncIterator[str] | Iterator[str] = follow_sse(
                ledger,
                task_id=task_id,
                initial_events=initial_events,
                last_event_id=resolved_cursor,
                request=request,
                poll_seconds=poll_seconds,
                heartbeat_seconds=heartbeat_seconds,
                retry_milliseconds=retry_milliseconds,
                terminal_event_kinds=terminal_event_kinds,
            )
        else:
            replay_payloads = replay_sse(
                ledger,
                task_id=task_id,
                last_event_id=resolved_cursor,
            )

            def generate() -> Iterator[str]:
                yield from replay_payloads

            payloads = generate()
        return StreamingResponse(
            payloads,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router
