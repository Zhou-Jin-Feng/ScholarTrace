from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_task_authorization import policy

from scholartrace.delivery.authorization import AuthorizationError, PlanningAuthorization
from scholartrace.delivery.models import ApprovalRequest, ExecutionMode, TaskCreateRequest
from scholartrace.delivery.plans import BudgetPlan, PlanOrigin, SourceScope
from scholartrace.delivery.service import M6TaskService, RealExecutionUnavailable, TaskStateError

ROOT = Path(__file__).resolve().parents[1]


def authorization():
    return PlanningAuthorization(
        policy_sha256=policy().digest(), max_cny="1", max_remote_calls=4,
        max_local_calls=2, max_external_requests=10, max_wall_clock_seconds=600,
        deadline_at=(datetime.now(UTC) + timedelta(seconds=590)).isoformat(), generation=1,
        planning={"max_cny": "0.2", "max_remote_calls": 1,
                  "max_local_calls": 0, "max_external_requests": 0},
    )


def test_real_gate_a_requires_explicit_policy_and_reack_does_not_refill(tmp_path):
    service = M6TaskService(root=ROOT, data_dir=tmp_path, runtime_policy=policy())
    try:
        task = service.create_task(TaskCreateRequest(question="synthetic question",
                                                    execution_mode=ExecutionMode.REAL))
        task_id = task["task_id"]
        with pytest.raises(AuthorizationError):
            service.acknowledge_plan_cost(task_id, acknowledged_max_cny=0.2)
        approved = authorization()
        result = service.acknowledge_plan_cost(task_id, acknowledged_max_cny=0.2,
                                               authorization=approved)
        before = service.budget.get(task_id)
        repeated = service.acknowledge_plan_cost(task_id, acknowledged_max_cny=0.2,
                                                 authorization=approved)
        assert result["authorization_id"] == repeated["authorization_id"]
        assert service.budget.get(task_id) == before
        assert service.effects.has_authorization(task_id)
        assert service.effects.db.execute(
            "SELECT state FROM phase_authorizations"
        ).fetchone()[0] == "active"
        with pytest.raises(RealExecutionUnavailable):
            service.generate_plan(task_id)
    finally:
        service.close()


def test_gate_a_audit_failure_leaves_pending_and_can_reconcile(tmp_path, monkeypatch):
    service = M6TaskService(root=ROOT, data_dir=tmp_path, runtime_policy=policy())
    try:
        task = service.create_task(TaskCreateRequest(question="synthetic question",
                                                    execution_mode=ExecutionMode.REAL))
        approved = authorization()

        def fail(**kwargs):
            raise OSError("synthetic audit failure")

        with monkeypatch.context() as patch:
            patch.setattr(service.ledger, "append_event", fail)
            with pytest.raises(OSError):
                service.acknowledge_plan_cost(task["task_id"], acknowledged_max_cny=0.2,
                                               authorization=approved)
        assert service.effects.db.execute(
            "SELECT state FROM phase_authorizations"
        ).fetchone()[0] == "pending"
        before = service.budget.get(task["task_id"])
        service.acknowledge_plan_cost(task["task_id"], acknowledged_max_cny=0.2,
                                       authorization=approved)
        assert service.budget.get(task["task_id"]) == before
        assert service.effects.db.execute(
            "SELECT state FROM phase_authorizations"
        ).fetchone()[0] == "active"
    finally:
        service.close()


@pytest.mark.parametrize("audit_failure", [False, True])
def test_gate_b_binds_original_capacity_and_waits_for_audit(tmp_path, monkeypatch, audit_failure):
    service = M6TaskService(root=ROOT, data_dir=tmp_path, runtime_policy=policy())
    observed = []
    try:
        task = service.create_task(TaskCreateRequest(question="synthetic question",
                                                    execution_mode=ExecutionMode.REAL))
        task_id = task["task_id"]
        service.acknowledge_plan_cost(task_id, acknowledged_max_cny=0.2,
                                       authorization=authorization())
        original = service.budget.get(task_id)
        plan = service.plans.save_plan(
            task_id=task_id, generated_by=PlanOrigin.MANUAL, sub_questions=("synthetic",),
            source_scope=SourceScope(providers=("arxiv",), year_from=2020, year_to=2025,
                                     max_papers=3),
            exclusions=(), budget_plan=BudgetPlan(max_cny=0.8, max_api_calls=3,
                                                 max_wall_clock_seconds=500,
                                                 estimate_source="synthetic"),
        )
        bare = dict(action="approve", plan_version=plan.plan_version, plan_digest=plan.plan_digest)
        with pytest.raises(AuthorizationError):
            service.approve_task(task_id, ApprovalRequest(**bare))
        request = ApprovalRequest(**bare, execution_authorization={
            "policy_sha256": policy().digest(), "generation": 1, "max_cny": "0.8",
            "max_remote_calls": 3, "max_local_calls": 2, "max_external_requests": 10,
        })

        def worker(task_id, cancel_event):
            observed.append(service.effects.db.execute(
                "SELECT state FROM phase_authorizations WHERE phase='execution'"
            ).fetchone()[0])

        monkeypatch.setattr(service, "_execute_queued", worker)
        if audit_failure:
            def fail(task_id):
                raise OSError("synthetic Gate B audit failure")
            monkeypatch.setattr(service, "_record_reservation_event", fail)
            with pytest.raises(TaskStateError, match="interrupted"):
                service.approve_task(task_id, request)
            assert not observed
            assert service.effects.db.execute(
                "SELECT state FROM phase_authorizations WHERE phase='execution'"
            ).fetchone()[0] == "pending"
        else:
            service.approve_task(task_id, request)
            assert observed == ["active"]
            bound = service.budget.get(task_id)
            assert bound.reserved_cny == original.reserved_cny
            assert bound.reserved_at == original.reserved_at
            assert bound.plan_digest == plan.plan_digest
            assert service.effects.db.execute(
                "SELECT state FROM phase_authorizations WHERE phase='planning'"
            ).fetchone()[0] == "revoked"
    finally:
        service.close()


def test_budget_report_never_substitutes_stale_zero_for_failed_projection(tmp_path, monkeypatch):
    import asyncio
    import threading

    from scholartrace.delivery.authorization import CallContext, CallUsage
    from scholartrace.delivery.models import BudgetReport

    service = M6TaskService(root=ROOT, data_dir=tmp_path, runtime_policy=policy())
    try:
        task = service.create_task(TaskCreateRequest(question="synthetic question",
                                                    execution_mode=ExecutionMode.REAL))
        task_id = task["task_id"]
        service.acknowledge_plan_cost(task_id, acknowledged_max_cny=0.2,
                                       authorization=authorization())
        ctx = CallContext(authorization_id="planning:1", operation_id="plan:1",
                          call_kind="remote_model", policy_sha256=policy().digest())

        async def operation():
            return {"cost": 0.1}

        asyncio.run(service.effects.run(
            task_id=task_id, key=ctx.effect_key(), request={"synthetic": True},
            operation=operation, max_cny=1, max_calls=4, reserve_cny=0.2, reserve_calls=1,
            measure=lambda r: r["cost"],
            usage=lambda r: CallUsage(input_tokens=10, output_tokens=2),
            cancel_event=threading.Event(), context=ctx,
        ))

        def fail(**kwargs):
            raise OSError("synthetic projection failure")

        with monkeypatch.context() as patch:
            patch.setattr(service.ledger, "project_confirmed_usage", fail)
            report = BudgetReport.model_validate(service.budget_report(task_id))
            assert report.measured_usage is None
            assert report.projection_state == "pending"
            assert report.journal_accounting.known_cny == 0.1
        report = BudgetReport.model_validate(service.budget_report(task_id))
        assert report.projection_state == "synced"
        assert report.measured_usage.external_cost_cny == 0.1
    finally:
        service.close()


def test_cancellation_revokes_authorization_without_erasing_capacity(tmp_path, monkeypatch):
    service = M6TaskService(root=ROOT, data_dir=tmp_path, runtime_policy=policy())
    try:
        task = service.create_task(TaskCreateRequest(question="synthetic question",
                                                    execution_mode=ExecutionMode.REAL))
        task_id = task["task_id"]
        service.acknowledge_plan_cost(task_id, acknowledged_max_cny=0.2,
                                       authorization=authorization())
        monkeypatch.setattr(service, "_write_exports", service.summary)
        service.cancel_task(task_id)
        assert service.effects.has_authorization(task_id)
        with pytest.raises(AuthorizationError, match="revoked"):
            service.effects.approved_policy(task_id)
        assert service.budget.get(task_id).reserved_cny == 1
    finally:
        service.close()
