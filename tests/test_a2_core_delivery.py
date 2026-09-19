from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from a2_core_fixtures import offline_pipeline

ROOT = Path(__file__).resolve().parents[1]


def test_approved_offline_research_is_readable_through_task_scoped_http_contracts(tmp_path):
    from scholartrace.api.app import create_app

    def runner(question, plan, cancel_event):
        async def run():
            async with offline_pipeline(tmp_path / "research") as (pipeline, _):
                return await pipeline.run(question=question, plan=plan, cancel_event=cancel_event)

        return asyncio.run(run())

    app = create_app(
        root=ROOT,
        data_dir=tmp_path / "delivery",
        offline_research_runner=runner,
        completion_wait_seconds=3,
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local.test"
        ) as http:
            response = await http.post(
                "/api/v1/research/tasks",
                json={"question": "What determines retrieval?", "execution_mode": "demo"},
            )
            task_id = response.json()["task_id"]
            base = f"/api/v1/research/tasks/{task_id}"
            assert (await http.get(base + "/claims")).status_code == 409
            await http.post(base + "/plan/acknowledge-cost", json={"acknowledged_max_cny": 0})
            plan = (await http.post(base + "/plan/generate")).json()
            approved = await http.post(
                base + "/approve",
                json={
                    "action": "approve",
                    "plan_version": plan["plan_version"],
                    "plan_digest": plan["plan_digest"],
                },
            )
            assert approved.status_code == 200
            task = approved.json()
            assert task["status"] == "degraded"
            assert task["metrics"]["execution_mode"] == "offline_fixture"
            assert task["metrics"]["paper_count"] == 3
            claims = (await http.get(base + "/claims")).json()
            assert len(claims["claims"]) == 3
            assert claims["excluded_count"] == 1
            evidence_id = claims["claims"][0]["evidence_ids"][0]
            evidence = await http.get(base + "/evidence/" + evidence_id)
            assert evidence.status_code == 200
            assert evidence.json()["binding"]["documind_version"] == "3.0.0"
            assert evidence.json()["excerpt_is_verbatim"] is True
            result = (await http.get(base + "/research-result")).json()
            assert result["plan_digest"] == plan["plan_digest"]
            assert str(tmp_path) not in str(result)
            timeline = (await http.get(base + "/timeline")).json()
            assert timeline["events"][-1]["kind"] == "exports_ready"
            assert {"retrieval_completed", "verification_completed", "citations_completed"} <= {
                event["kind"] for event in timeline["events"]
            }
            report = await http.get(base + "/report?format=markdown")
            assert "[PARTIALLY SUPPORTED]" in report.text
            other = (
                await http.post("/api/v1/research/tasks", json={"question": "Other task"})
            ).json()
            cross = await http.get(
                f"/api/v1/research/tasks/{other['task_id']}/evidence/{evidence_id}"
            )
            assert cross.status_code == 404

    try:
        asyncio.run(run())
    finally:
        app.state.m6_service.close()


def test_new_read_and_recovery_routes_keep_bearer_authentication(tmp_path):
    from scholartrace.api.app import create_app

    app = create_app(
        root=ROOT,
        data_dir=tmp_path,
        deployment_mode="trusted_private",
        auth_token="synthetic-private-token",
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local.test"
        ) as http:
            for endpoint in ("claims", "research-result", "timeline", "evidence/missing"):
                response = await http.get(f"/api/v1/research/tasks/missing/{endpoint}")
                assert response.status_code == 401
            response = await http.post(
                "/api/v1/research/tasks/missing/resume",
                json={"plan_version": 1, "plan_digest": "0" * 64},
            )
            assert response.status_code == 401
            response = await http.get(
                "/api/v1/research/tasks/missing/claims",
                headers={"Authorization": "Bearer synthetic-private-token"},
            )
            assert response.status_code == 404

    try:
        asyncio.run(run())
    finally:
        app.state.m6_service.close()


def test_new_public_views_have_deterministic_checked_in_schemas():
    import json

    from scholartrace.delivery.views import VIEW_CONTRACT_MODELS

    for name, model in VIEW_CONTRACT_MODELS.items():
        assert json.loads(
            (ROOT / f"contracts/schemas/{name}.schema.json").read_text("utf-8")
        ) == model.model_json_schema(mode="validation")
