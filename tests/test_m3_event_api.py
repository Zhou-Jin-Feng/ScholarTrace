from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi import FastAPI

from scholartrace.api import create_event_router
from scholartrace.workflow.storage import RuntimeLedger


def test_sse_endpoint_replays_after_last_event_id(tmp_path: Path) -> None:
    async def scenario() -> None:
        ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
        try:
            first = ledger.append_event(
                stable_key="event:sse:1",
                task_id="task:sse",
                node="search",
                kind="started",
            )
            second = ledger.append_event(
                stable_key="event:sse:2",
                task_id="task:sse",
                node="search",
                kind="finished",
            )
            app = FastAPI()
            app.include_router(create_event_router(ledger))
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/v1/research/tasks/task:sse/events",
                    headers={"Last-Event-ID": first.event_id},
                )

            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert f"id: {second.event_id}" in response.text
            assert f"id: {first.event_id}" not in response.text
            assert response.headers["cache-control"] == "no-cache"
        finally:
            ledger.close()

    asyncio.run(scenario())


def test_sse_endpoint_rejects_invalid_last_event_id(tmp_path: Path) -> None:
    async def scenario() -> None:
        ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
        try:
            app = FastAPI()
            app.include_router(create_event_router(ledger))
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/v1/research/tasks/task:sse/events",
                    headers={"Last-Event-ID": "bad"},
                )
            assert response.status_code == 400
        finally:
            ledger.close()

    asyncio.run(scenario())
