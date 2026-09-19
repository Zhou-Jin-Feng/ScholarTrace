"""Invalid limits and unconfirmed external effects cannot release capacity."""

from pathlib import Path

import pytest
from test_a2_budget import _reserve, _store

from scholartrace.contracts import BudgetLimits, BudgetUsage
from scholartrace.delivery.budget import BudgetError
from scholartrace.delivery.models import ExecutionMode, TaskCreateRequest, TaskStatus
from scholartrace.delivery.plans import BudgetPlan
from scholartrace.delivery.service import M6TaskService


@pytest.mark.parametrize("amount", [-1, float("inf"), float("nan")])
def test_invalid_reservation_is_rejected(tmp_path, amount):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        _reserve(store, "task:invalid", amount)
    assert store.get("task:invalid") is None


@pytest.mark.parametrize("calls,seconds", [(-1, 60), (1, -1), (1.5, 60), (True, 60)])
def test_invalid_plan_limits_are_rejected(calls, seconds):
    with pytest.raises(ValueError):
        BudgetPlan(1.0, calls, seconds, "fixture")


@pytest.mark.parametrize("mode", [ExecutionMode.DEMO, ExecutionMode.REAL])
def test_restart_retains_known_usage_and_unknown_exposure(tmp_path: Path, mode):
    service = M6TaskService(root=tmp_path, data_dir=tmp_path)
    task_id = service.create_task(
        TaskCreateRequest(question="Accounting recovery", execution_mode=mode)
    )["task_id"]
    _reserve(service.budget, task_id, 10.0)
    service.ledger.charge(
        effect_key="effect:measured",
        task_id=task_id,
        delta=BudgetUsage(external_cost_cny=2.5, api_calls=1),
        limits=BudgetLimits(),
    )
    service.store.update_task(task_id, status=TaskStatus.FAILED)
    service.close()
    for _ in range(2):
        recovered = M6TaskService(root=tmp_path, data_dir=tmp_path)
        try:
            reservation = recovered.budget.get(task_id)
            assert reservation is not None
            if mode == ExecutionMode.DEMO:
                assert reservation.settled_cny == 2.5
                assert reservation.settled_api_calls == 1
            else:
                assert reservation.is_open
                assert reservation.reconciliation_required
                assert reservation.recorded_cny == 2.5
                assert recovered.budget.committed_cny() == 10.0
                assert recovered.budget.release(task_id, reason="retry") is False
            assert recovered.ledger.usage(task_id).external_cost_cny == 2.5
        finally:
            recovered.close()


def test_unknown_orphan_does_not_become_zero(tmp_path):
    store = _store(tmp_path)
    _reserve(store, "task:unknown", 10)
    store.reconcile_orphans(terminal_task_ids={"task:unknown"})
    reservation = store.get("task:unknown")
    assert reservation.is_open
    assert reservation.settled_cny is None
    assert store.committed_cny() == 10


def test_conflicting_settlement_refused(tmp_path):
    store = _store(tmp_path)
    _reserve(store, "task:one", 10)
    store.settle(task_id="task:one", settled_cny=2, settled_api_calls=1, reason="done")
    with pytest.raises(BudgetError):
        store.settle(task_id="task:one", settled_cny=0, settled_api_calls=0, reason="retry")


def test_decimal_capacity_boundary_is_inclusive(tmp_path):
    store = _store(tmp_path)
    _reserve(store, "task:first", 0.1, ceiling_cny=0.3)
    _reserve(store, "task:second", 0.2, ceiling_cny=0.3)


@pytest.mark.parametrize(
    "projected",
    [
        BudgetUsage(external_cost_cny=11),
        BudgetUsage(api_calls=11),
        BudgetUsage(elapsed_seconds=601),
    ],
)
def test_pre_dispatch_guard_rejects_excess_usage(tmp_path, projected):
    store = _store(tmp_path)
    _reserve(store, "task:guard", 10)
    with pytest.raises(BudgetError):
        store.check_usage("task:guard", projected)


def test_service_enforces_explicit_shared_capacity_without_approving_plan(tmp_path):
    from scholartrace.delivery.models import ApprovalRequest
    from scholartrace.delivery.plans import PlanOrigin, SourceScope

    service = M6TaskService(root=tmp_path, data_dir=tmp_path, reservation_capacity_cny=1)
    try:
        task_id = service.create_task(TaskCreateRequest(question="Capacity test"))["task_id"]
        plan = service.plans.save_plan(
            task_id=task_id,
            generated_by=PlanOrigin.MANUAL,
            sub_questions=("Question",),
            source_scope=SourceScope(("arxiv",), 2020, 2025),
            exclusions=(),
            budget_plan=BudgetPlan(2, 1, 60, "fixture"),
            require_acknowledgement=False,
        )
        with pytest.raises(BudgetError):
            service.approve_task(
                task_id,
                ApprovalRequest(
                    action="approve", plan_version=plan.plan_version, plan_digest=plan.plan_digest
                ),
            )
        assert service.current_plan(task_id)["approval_state"] == "waiting_approval"
        assert service.budget.get(task_id) is None
    finally:
        service.close()


def test_running_real_reservation_is_unconfirmed_after_restart(tmp_path):
    service = M6TaskService(root=tmp_path, data_dir=tmp_path)
    task_id = service.create_task(
        TaskCreateRequest(question="Interrupted call", execution_mode=ExecutionMode.REAL)
    )["task_id"]
    _reserve(service.budget, task_id, 10)
    service.store.update_task(task_id, status=TaskStatus.RUNNING)
    service.close()
    service = M6TaskService(root=tmp_path, data_dir=tmp_path)
    try:
        assert service.budget.get(task_id).reconciliation_required
        assert service.budget.committed_cny() == 10
        with pytest.raises(BudgetError):
            service.budget.check_usage(task_id, BudgetUsage())
    finally:
        service.close()
