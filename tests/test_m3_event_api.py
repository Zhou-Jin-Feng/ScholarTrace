from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
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
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/api/v1/research/tasks/task:sse/events",
                    headers={"Last-Event-ID": first.event_id},
                )

            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert f"id: {second.event_id}" in response.text
            assert f"id: {first.event_id}" not in response.text
            assert "no-cache" in response.headers["cache-control"]
            assert "no-transform" in response.headers["cache-control"]
        finally:
            ledger.close()

    asyncio.run(scenario())


def test_sse_follow_stream_emits_heartbeat_new_event_and_terminal_signal(tmp_path: Path) -> None:
    async def scenario() -> None:
        ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
        try:
            first = ledger.append_event(
                stable_key="event:sse:follow:1",
                task_id="task:sse-follow",
                node="search",
                kind="started",
            )
            app = FastAPI()
            app.include_router(
                create_event_router(
                    ledger,
                    poll_seconds=0.01,
                    heartbeat_seconds=0.015,
                    retry_milliseconds=25,
                    terminal_event_kinds=frozenset({"finished"}),
                )
            )

            async def finish() -> None:
                await asyncio.sleep(0.04)
                ledger.append_event(
                    stable_key="event:sse:follow:2",
                    task_id="task:sse-follow",
                    node="search",
                    kind="finished",
                )

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                finisher = asyncio.create_task(finish())
                response = await client.get(
                    "/api/v1/research/tasks/task:sse-follow/events?follow=true"
                )
                await finisher

            assert response.status_code == 200
            assert response.text.startswith("retry: 25\n\n")
            assert f"id: {first.event_id}" in response.text
            assert ": heartbeat\n\n" in response.text
            assert "event: finished" in response.text
            assert 'event: stream_end\ndata: {"reason":"terminal"}' in response.text
        finally:
            ledger.close()

    asyncio.run(scenario())


def test_sse_follow_reconnect_after_terminal_cursor_closes_immediately(tmp_path: Path) -> None:
    async def scenario() -> None:
        ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
        try:
            terminal = ledger.append_event(
                stable_key="event:sse:terminal",
                task_id="task:sse-terminal",
                node="delivery",
                kind="exports_ready",
            )
            app = FastAPI()
            app.include_router(
                create_event_router(ledger, poll_seconds=0.01, heartbeat_seconds=0.02)
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/api/v1/research/tasks/task:sse-terminal/events?follow=true",
                    headers={"Last-Event-ID": terminal.event_id},
                )

            assert response.status_code == 200
            assert (
                response.text == 'retry: 1000\n\nevent: stream_end\ndata: {"reason":"terminal"}\n\n'
            )
        finally:
            ledger.close()

    asyncio.run(scenario())


def test_sse_cursor_must_belong_to_requested_task(tmp_path: Path) -> None:
    async def scenario() -> None:
        ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
        try:
            foreign = ledger.append_event(
                stable_key="event:sse:foreign",
                task_id="task:foreign",
                node="search",
                kind="finished",
            )
            ledger.append_event(
                stable_key="event:sse:local",
                task_id="task:local",
                node="search",
                kind="finished",
            )
            app = FastAPI()
            app.include_router(create_event_router(ledger))
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/api/v1/research/tasks/task:local/events",
                    headers={"Last-Event-ID": foreign.event_id},
                )
                conflicting = await client.get(
                    "/api/v1/research/tasks/task:local/events",
                    headers={"Last-Event-ID": "event:2"},
                    params={"last_event_id": "event:1"},
                )

            assert response.status_code == 400
            assert response.json()["detail"] == "invalid Last-Event-ID"
            assert conflicting.status_code == 400
            assert conflicting.json()["detail"] == "conflicting Last-Event-ID"
        finally:
            ledger.close()

    asyncio.run(scenario())


def test_sse_router_rejects_non_positive_timing(tmp_path: Path) -> None:
    ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
    try:
        with pytest.raises(ValueError, match="timing values"):
            create_event_router(ledger, poll_seconds=0)
    finally:
        ledger.close()


def test_sse_endpoint_rejects_invalid_last_event_id(tmp_path: Path) -> None:
    async def scenario() -> None:
        ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
        try:
            app = FastAPI()
            app.include_router(create_event_router(ledger))
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/api/v1/research/tasks/task:sse/events",
                    headers={"Last-Event-ID": "bad"},
                )
            assert response.status_code == 400
        finally:
            ledger.close()

    asyncio.run(scenario())
