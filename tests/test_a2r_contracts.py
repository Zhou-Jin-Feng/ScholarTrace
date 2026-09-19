"""Delivery HTTP contracts use the same editable plan and budget shapes."""

import asyncio

import httpx

from scholartrace.api.app import create_app
from scholartrace.delivery.models import BudgetReport, ResearchPlanView, TaskSummary


async def _contract_scenario(tmp_path):
    app = create_app(root=tmp_path, data_dir=tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        task = (
            await client.post("/api/v1/research/tasks", json={"question": "Contract consistency"})
        ).json()
        base = "/api/v1/research/tasks/" + task["task_id"]
        assert (await client.post(base + "/plan/generate")).status_code == 409
        assert (await client.post(base + "/plan/acknowledge-cost", json={})).status_code == 422
        assert (
            await client.post(base + "/plan/acknowledge-cost", json={"acknowledged_max_cny": 0})
        ).status_code == 200
        plan = (await client.post(base + "/plan/generate")).json()
        ResearchPlanView.model_validate(plan)
        edit = {
            key: plan[key] for key in ("sub_questions", "source_scope", "exclusions", "budget_plan")
        }
        edit["sub_questions"] = ["Revised scope"]
        response = await client.post(
            base + "/approve",
            json={
                "action": "modify",
                "plan_version": plan["plan_version"],
                "plan_digest": plan["plan_digest"],
                "modified_plan": edit,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "waiting_approval"
        updated = (await client.get(base + "/plan")).json()
        assert updated["plan_version"] == 2
        assert updated["sub_questions"] == ["Revised scope"]
        budget = (await client.get(base + "/budget")).json()
        BudgetReport.model_validate(budget)
        assert budget["reservation"] is None
        result = await client.post(
            base + "/approve",
            json={
                "action": "approve",
                "plan_version": updated["plan_version"],
                "plan_digest": updated["plan_digest"],
            },
        )
        assert result.status_code == 200
        budget = (await client.get(base + "/budget")).json()
        BudgetReport.model_validate(budget)
        assert budget["reservation"]["settled"]["cny"] == 0
        for item in (await client.get("/api/v1/research/tasks")).json()["items"]:
            TaskSummary.model_validate(item)
            assert "paper_count" not in item  # unknown counts are not fabricated zeros
        assert (await client.post(base + "/plan/generate")).status_code == 409

    app.state.m6_service.close()


def test_ack_generate_modify_and_budget_contracts(tmp_path):
    asyncio.run(_contract_scenario(tmp_path))
