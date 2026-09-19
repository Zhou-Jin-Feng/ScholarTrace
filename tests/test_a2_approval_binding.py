"""F02 at the HTTP layer: an approval must name the plan revision it decides on.

The pre-A2 API accepted a bare ``{"action": "approve"}``, so a decision could
land on a plan the reviewer never read. These tests drive the real endpoints
rather than PlanStore directly, because the regression they guard against was a
missing binding in the route, not in the store.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from scholartrace.api.app import create_app

PAYLOAD = {
    "title": "F02 binding",
    "question": "Does an approval have to name the plan version it approves?",
    "demo_mode": "success",
}


def _client(tmp_path: Path) -> httpx.AsyncClient:
    app = create_app(root=Path(__file__).resolve().parents[1], data_dir=tmp_path)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _task_with_plan(client: httpx.AsyncClient, key: str) -> tuple[str, dict[str, object]]:
    """Create a task and walk Gate A so it owns a persisted plan version."""

    created = await client.post(
        "/api/v1/research/tasks", json=PAYLOAD, headers={"Idempotency-Key": key}
    )
    assert created.status_code == 201
    task_id = str(created.json()["task_id"])

    estimate = await client.post(f"/api/v1/research/tasks/{task_id}/plan/estimate")
    assert estimate.status_code == 200
    ceiling = float(estimate.json()["estimated_cny"])

    ack = await client.post(
        f"/api/v1/research/tasks/{task_id}/plan/acknowledge-cost",
        json={"acknowledged_max_cny": ceiling},
    )
    assert ack.status_code == 200

    generated = await client.post(f"/api/v1/research/tasks/{task_id}/plan/generate")
    assert generated.status_code == 200
    return task_id, generated.json()


def test_approve_without_plan_version_is_refused(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as client:
            task_id, _plan = await _task_with_plan(client, "f02-missing")

            bare = await client.post(
                f"/api/v1/research/tasks/{task_id}/approve",
                json={"action": "approve", "reason": "looks fine"},
            )
            assert bare.status_code == 409
            assert bare.json()["detail"]["code"] == "plan_version_required"

            # The refusal must not have advanced the task.
            after = await client.get(f"/api/v1/research/tasks/{task_id}")
            assert after.json()["status"] == "waiting_approval"

    asyncio.run(scenario())


def test_approve_with_stale_digest_is_refused(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as client:
            task_id, plan = await _task_with_plan(client, "f02-stale")
            version = int(plan["plan_version"])  # type: ignore[call-overload]

            stale = await client.post(
                f"/api/v1/research/tasks/{task_id}/approve",
                json={
                    "action": "approve",
                    "reason": "approving a digest I did not read",
                    "plan_version": version,
                    "plan_digest": "0" * 64,
                },
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["code"] == "plan_version_stale"

            after = await client.get(f"/api/v1/research/tasks/{task_id}")
            assert after.json()["status"] == "waiting_approval"

    asyncio.run(scenario())


def test_approve_with_matching_version_and_digest_succeeds(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as client:
            task_id, plan = await _task_with_plan(client, "f02-match")

            approved = await client.post(
                f"/api/v1/research/tasks/{task_id}/approve",
                json={
                    "action": "approve",
                    "reason": "read version 1",
                    "plan_version": int(plan["plan_version"]),  # type: ignore[call-overload]
                    "plan_digest": str(plan["plan_digest"]),
                },
            )
            assert approved.status_code == 200
            assert approved.json()["status"] in {"queued", "running", "completed", "degraded"}

            # A replay of the same decision must not be accepted a second time
            # as a fresh approval of a plan that already has a verdict.
            replay = await client.post(
                f"/api/v1/research/tasks/{task_id}/approve",
                json={
                    "action": "approve",
                    "reason": "read version 1",
                    "plan_version": int(plan["plan_version"]),  # type: ignore[call-overload]
                    "plan_digest": str(plan["plan_digest"]),
                },
            )
            assert replay.status_code == 409

    asyncio.run(scenario())


def test_plan_versions_endpoint_lists_the_generated_version(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _client(tmp_path) as client:
            task_id, plan = await _task_with_plan(client, "f02-versions")

            listed = await client.get(f"/api/v1/research/tasks/{task_id}/plan/versions")
            assert listed.status_code == 200
            versions = listed.json()["versions"]
            assert [v["plan_version"] for v in versions] == [int(plan["plan_version"])]  # type: ignore[call-overload]

            # The fixture origin must stay visible: this is not a real
            # Coordinator plan and the UI has to be able to say so (ADR-014).
            current = await client.get(f"/api/v1/research/tasks/{task_id}/plan")
            assert current.json()["generated_by"] == "fixture"

    asyncio.run(scenario())
