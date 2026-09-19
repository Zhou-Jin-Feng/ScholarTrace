from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from test_n2_authorization_service import authorization
from test_task_authorization import policy

from scholartrace.api.app import create_app


def test_http_explicit_authorization_and_probe_permission(tmp_path, monkeypatch):
    token = "synthetic-test-bearer-only"
    app = create_app(root=Path(__file__).resolve().parents[1], data_dir=tmp_path,
                     runtime_policy=policy(), deployment_mode="trusted_private", auth_token=token)
    service = app.state.m6_service
    probe_calls = []

    async def synthetic_probe(client):
        probe_calls.append(True)
        return service.dependency_report()

    monkeypatch.setattr(service, "probe_dependencies", synthetic_probe)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://test") as client:
            assert (await client.post("/api/v1/health/dependencies/probe")).status_code == 401
            assert not probe_calls
            headers = {"Authorization": "Bearer " + token}
            assert (await client.post("/api/v1/health/dependencies/probe",
                                      headers=headers)).status_code == 200
            assert len(probe_calls) == 1
            created = await client.post("/api/v1/research/tasks", headers=headers,
                                        json={"question": "synthetic", "execution_mode": "real"})
            assert created.status_code == 201
            task_id = created.json()["task_id"]
            route = f"/api/v1/research/tasks/{task_id}/plan/acknowledge-cost"
            refused = await client.post(route, headers=headers, json={"acknowledged_max_cny": 0.2})
            assert refused.status_code == 409
            assert refused.json()["detail"]["code"] == "authorization_refused"
            payload = {"acknowledged_max_cny": 0.2,
                       "authorization": authorization().model_dump(mode="json")}
            allowed = await client.post(route, headers=headers, json=payload)
            assert allowed.status_code == 200
            assert allowed.json()["authorization_id"] == "planning:1"
            report = await client.get(f"/api/v1/research/tasks/{task_id}/budget", headers=headers)
            assert report.status_code == 200
            assert report.json()["journal_accounting"]["known_cny"] == 0
            assert report.json()["is_actual_bill"] is False
    try:
        asyncio.run(run())
    finally:
        service.close()
