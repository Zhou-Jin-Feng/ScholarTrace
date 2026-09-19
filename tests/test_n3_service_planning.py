from __future__ import annotations

import asyncio
import threading
from datetime import date
from pathlib import Path

import pytest
from test_n2_authorization_service import authorization
from test_task_authorization import policy

from scholartrace.contracts import Budget, BudgetLimits, ResearchPlan, ResearchSubquestion
from scholartrace.delivery.authorization import CallContext, CallUsage
from scholartrace.delivery.models import ApprovalRequest, TaskCreateRequest
from scholartrace.delivery.service import M6TaskService, TaskStateError

ROOT = Path(__file__).resolve().parents[1]


class SyntheticPlanner:
    policy = policy()
    year_from = 2020
    evidence_kind = "synthetic"

    def __init__(self):
        self.calls = 0

    def generate_plan(self, *, task_id, question, journal, ledger, cancel_event):
        ctx = CallContext(
            authorization_id="planning:1",
            operation_id="coordinator:1",
            call_kind="remote_model",
            policy_sha256=self.policy.digest(),
        )

        async def operation():
            self.calls += 1
            draft = ResearchPlan(
                task_id=task_id,
                question=question,
                title="Synthetic plan",
                objective="Compare full-text evidence",
                inclusion_criteria=["Experimental reports"],
                exclusion_criteria=["Opinion pieces"],
                sources=["arxiv"],
                retrieval_cutoff=date(2026, 9, 18),
                status="draft",
                subquestions=[
                    ResearchSubquestion(
                        subquestion_id="subq:1",
                        question="First question",
                        evidence_required="fulltext",
                        priority="high",
                    ),
                    ResearchSubquestion(
                        subquestion_id="subq:2",
                        question="Second question",
                        evidence_required="fulltext",
                        priority="normal",
                    ),
                ],
                budget=Budget(
                    limits=BudgetLimits(
                        max_cost_cny=0.8,
                        max_api_calls=3,
                        max_fulltext_papers=3,
                        max_duration_seconds=500,
                    )
                ),
            )
            return {"plan": draft.model_dump(mode="json"), "cost": 0.1}

        result = asyncio.run(
            journal.run(
                task_id=task_id,
                key=ctx.effect_key(),
                request={"question": question},
                operation=operation,
                max_cny=1,
                max_calls=4,
                reserve_cny=0.2,
                reserve_calls=1,
                measure=lambda r: r["cost"],
                usage=lambda r: CallUsage(input_tokens=10, output_tokens=2),
                cancel_event=cancel_event,
                context=ctx,
            )
        )
        return ResearchPlan.model_validate(result["plan"])

    def run(self, **kwargs):
        raise AssertionError("planning cannot dispatch research")


def test_coordinator_mapping_preserves_all_details_and_retry_does_not_regenerate(
    tmp_path, monkeypatch
):
    runner = SyntheticPlanner()
    service = M6TaskService(root=ROOT, data_dir=tmp_path, live_runner=runner)
    try:
        task_id = service.create_task(
            TaskCreateRequest(question="Synthetic research question", execution_mode="real")
        )["task_id"]
        service.acknowledge_plan_cost(
            task_id, acknowledged_max_cny=0.2, authorization=authorization()
        )
        original = service.plans.save_plan

        def fail(**kwargs):
            raise OSError("synthetic metadata write failure")

        with monkeypatch.context() as patch:
            patch.setattr(service.plans, "save_plan", fail)
            with pytest.raises(OSError):
                service.generate_plan(task_id)
        assert runner.calls == 1
        assert service.plans.save_plan == original
        plan = service.generate_plan(task_id)
        assert plan["sub_questions"] == ["First question", "Second question"]
        assert plan["details"]["inclusion_criteria"] == ["Experimental reports"]
        assert plan["details"]["subquestions"][0]["priority"] == "high"
        assert plan["details"]["retrieval_cutoff"] == "2026-09-18"
        assert service.generate_plan(task_id) == plan
        assert runner.calls == 1
        assert service.budget_report(task_id)["journal_accounting"]["known_cny"] == 0.1
        assert service.summary(task_id)["status"] == "waiting_approval"
        edited = {
            k: plan[k] for k in ("sub_questions", "source_scope", "exclusions", "budget_plan")
        }
        with pytest.raises(TaskStateError, match="detailed"):
            service.approve_task(
                task_id,
                ApprovalRequest(
                    action="modify",
                    plan_version=plan["plan_version"],
                    plan_digest=plan["plan_digest"],
                    modified_plan=edited,
                ),
            )
        edited["details"] = plan["details"]
        service.approve_task(
            task_id,
            ApprovalRequest(
                action="modify",
                plan_version=plan["plan_version"],
                plan_digest=plan["plan_digest"],
                modified_plan=edited,
            ),
        )
        current = service.current_plan(task_id)
        assert current["generated_by"] == "manual"
        assert current["details"] == plan["details"]
        assert current["approval_state"] == "waiting_approval"
        assert current["plan_version"] == 2
        assert runner.calls == 1
    finally:
        service.close()


def test_close_signals_active_planning_then_finishes_after_planner_unwinds(tmp_path):
    runner = SyntheticPlanner()
    entered = threading.Event()
    release = threading.Event()
    original = runner.generate_plan

    def blocking_generate_plan(**kwargs):
        entered.set()
        release.wait(timeout=5)
        return original(**kwargs)

    runner.generate_plan = blocking_generate_plan
    service = M6TaskService(root=ROOT, data_dir=tmp_path, live_runner=runner)
    task_id = service.create_task(
        TaskCreateRequest(question="Close while planning", execution_mode="real")
    )["task_id"]
    service.acknowledge_plan_cost(
        task_id, acknowledged_max_cny=0.2, authorization=authorization()
    )
    errors = []

    def run_planning():
        try:
            service.generate_plan(task_id)
        except Exception as exc:  # cancellation is the expected close outcome
            errors.append(exc)

    worker = threading.Thread(target=run_planning, daemon=True)
    worker.start()
    assert entered.wait(timeout=5)
    service.close()
    assert not service._closed
    release.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert errors and type(errors[0]).__name__ == "EffectCancelledError"
    service.close()
    assert service._closed
