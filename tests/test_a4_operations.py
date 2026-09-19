"""Current delivery data survives restore, with new routes still fail-closed."""

import asyncio
import hashlib
import json
import zipfile
from pathlib import Path

import httpx
import pytest
from a2_core_fixtures import offline_pipeline

from scholartrace.api.app import create_app
from scholartrace.delivery.models import ApprovalRequest, TaskCreateRequest
from scholartrace.operations.backup import BackupError, backup_data, restore_data

ROOT = Path(__file__).resolve().parents[1]


def test_research_restore_keeps_plans_budget_claims_evidence_events_and_exports(tmp_path):
    def runner(question, plan, cancel_event):
        async def run():
            async with offline_pipeline(tmp_path / "research") as (pipeline, _):
                return await pipeline.run(question=question, plan=plan, cancel_event=cancel_event)

        return asyncio.run(run())

    data = tmp_path / "source"
    app = create_app(
        root=ROOT, data_dir=data, offline_research_runner=runner, completion_wait_seconds=5
    )
    service = app.state.m6_service
    try:
        task = service.create_task(TaskCreateRequest(question="What determines retrieval?"))
        task_id = task["task_id"]
        service.acknowledge_plan_cost(task_id, acknowledged_max_cny=0)
        plan = service.generate_plan(task_id)
        task = service.approve_task(
            task_id,
            ApprovalRequest(
                action="approve", plan_version=plan["plan_version"], plan_digest=plan["plan_digest"]
            ),
        )
        assert task["status"] == "degraded"
        assert task["metrics"]["execution_mode"] == "offline_fixture"
        evidence_id = service.claims(task_id)["claims"][0]["evidence_ids"][0]

        async def snapshot(application):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application), base_url="http://synthetic.test"
            ) as client:
                routes = [
                    "",
                    "/plan",
                    "/plan/versions",
                    "/budget",
                    "/claims",
                    "/research-result",
                    "/timeline",
                    "/evidence/" + evidence_id,
                ]
                routes += [f"/report?format={f}" for f in ("pdf", "markdown", "html", "json")]
                result = {}
                for route in routes:
                    response = await client.get(f"/api/v1/research/tasks/{task_id}{route}")
                    assert response.status_code == 200, route
                    result[route] = hashlib.sha256(response.content).hexdigest()
                return result

        before = asyncio.run(snapshot(app))
    finally:
        service.close()
    archive = tmp_path / "backup.zip"
    backup_data(data_dir=data, output_path=archive)
    restored = tmp_path / "restored"
    assert restore_data(archive_path=archive, target_dir=restored)["verified"]
    restored_app = create_app(root=ROOT, data_dir=restored)
    try:
        assert asyncio.run(snapshot(restored_app)) == before
    finally:
        restored_app.state.m6_service.close()
    # Same-size corruption must fail before publishing a restore directory.
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            content = source.read(info.filename)
            if info.filename.endswith("tasks.sqlite"):
                content = content[:-1] + bytes([content[-1] ^ 1])
            target.writestr(info, content)
    with pytest.raises(BackupError, match="hash does not match"):
        restore_data(archive_path=tampered, target_dir=tmp_path / "bad-restore")
    assert not (tmp_path / "bad-restore").exists()
    with zipfile.ZipFile(archive) as source:
        manifest = json.loads(source.read("backup-manifest.json"))
    assert manifest["application_version"] == "1.0.2"


@pytest.mark.parametrize(
    "route",
    ["claims", "evidence/missing", "research-result", "budget", "plan/versions", "timeline"],
)
def test_query_token_cannot_bypass_bearer_on_new_read_routes(tmp_path, route):
    token = "synthetic-a4-token-not-a-secret"
    app = create_app(
        root=ROOT, data_dir=tmp_path, deployment_mode="trusted_private", auth_token=token
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic.test"
        ) as client:
            url = f"/api/v1/research/tasks/missing/{route}"
            for headers in ({}, {"Authorization": "Bearer wrong-synthetic-token"}):
                response = await client.get(url, params={"access_token": token}, headers=headers)
                assert response.status_code == 401
            authorized = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            assert authorized.status_code == 404

    try:
        asyncio.run(run())
    finally:
        app.state.m6_service.close()
