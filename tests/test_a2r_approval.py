"""Service-level decisions never dispatch unreviewed or rolled-back work."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scholartrace.delivery.models import ApprovalRequest, ExecutionMode, TaskCreateRequest
from scholartrace.delivery.queue import QueueClosedError, QueueFullError
from scholartrace.delivery.service import M6TaskService, TaskStateError


@pytest.fixture
def service(tmp_path: Path):
    instance = M6TaskService(root=tmp_path, data_dir=tmp_path)
    yield instance
    instance.close()


def planned(service: M6TaskService):
    task_id = service.create_task(TaskCreateRequest(question="What evidence is available?"))[
        "task_id"
    ]
    service.acknowledge_plan_cost(task_id, acknowledged_max_cny=0)
    plan = service.generate_plan(task_id)
    return task_id, plan


def decision(plan, **kwargs):
    return ApprovalRequest(
        plan_version=plan["plan_version"], plan_digest=plan["plan_digest"], **kwargs
    )


def test_modify_creates_new_reviewable_version_without_queue_or_budget(service):
    task_id, plan = planned(service)
    modified = {
        key: plan[key] for key in ("sub_questions", "source_scope", "exclusions", "budget_plan")
    }
    modified["sub_questions"] = ["A revised evidence question"]
    result = service.approve_task(task_id, decision(plan, action="modify", modified_plan=modified))
    assert result["status"] == "waiting_approval"
    versions = service.plan_versions(task_id)["versions"]
    assert len(versions) == 2
    assert versions[0]["approval_state"] == "superseded"
    assert versions[1]["approval_state"] == "waiting_approval"
    assert versions[1]["sub_questions"] == modified["sub_questions"]
    assert versions[1]["plan_digest"] != plan["plan_digest"]
    assert service.budget.get(task_id) is None
    assert service.queue_snapshot()["submitted"] == 0


@pytest.mark.parametrize("error", [QueueFullError, QueueClosedError])
def test_rejected_queue_rolls_back_decision_and_can_retry(service, monkeypatch, error):
    task_id, plan = planned(service)
    original = service._executor.submit

    def refuse(*args, **kwargs):
        raise error("injected admission refusal")

    monkeypatch.setattr(service._executor, "submit", refuse)
    request = decision(plan, action="approve")
    with pytest.raises(TaskStateError):
        service.approve_task(task_id, request)
    assert service.summary(task_id)["status"] == "waiting_approval"
    assert service.current_plan(task_id)["approval_state"] == "waiting_approval"
    assert service.budget.get(task_id) is None
    monkeypatch.setattr(service._executor, "submit", original)
    assert service.approve_task(task_id, request)["status"] == "completed"


def test_concurrent_approval_executes_once(service):
    task_id, plan = planned(service)
    request = decision(plan, action="approve")

    def approve():
        try:
            service.approve_task(task_id, request)
            return "accepted"
        except ValueError:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: approve(), range(2))) == ["accepted", "refused"]
    assert len([e for e in service.events(task_id) if e["kind"] == "plan_approved"]) == 1


def test_real_task_without_plan_cannot_be_approved(service):
    task = service.create_task(
        TaskCreateRequest(question="Real research", execution_mode=ExecutionMode.REAL)
    )
    with pytest.raises(ValueError):
        service.approve_task(task["task_id"], ApprovalRequest(action="approve"))
    assert service.summary(task["task_id"])["status"] == "waiting_approval"


def test_modify_save_failure_keeps_previous_plan_reviewable(service, monkeypatch):
    task_id, plan = planned(service)
    modified = {
        key: plan[key] for key in ("sub_questions", "source_scope", "exclusions", "budget_plan")
    }

    def fail(**kwargs):
        raise RuntimeError("injected plan persistence error")

    monkeypatch.setattr(service.plans, "save_plan", fail)
    with pytest.raises(RuntimeError):
        service.approve_task(task_id, decision(plan, action="modify", modified_plan=modified))
    assert service.current_plan(task_id)["approval_state"] == "waiting_approval"
    assert len(service.plan_versions(task_id)["versions"]) == 1


def test_modify_cannot_increase_budget(service):
    task_id, plan = planned(service)
    modified = {
        key: plan[key] for key in ("sub_questions", "source_scope", "exclusions", "budget_plan")
    }
    modified["budget_plan"] = {**modified["budget_plan"], "max_cny": 1}
    with pytest.raises(TaskStateError):
        service.approve_task(task_id, decision(plan, action="modify", modified_plan=modified))
    assert service.current_plan(task_id)["approval_state"] == "waiting_approval"


def test_cancel_and_approval_serialize_without_reviving_a_cancelled_task(service):
    task_id, plan = planned(service)
    request = decision(plan, action="approve")

    def transition(operation):
        try:
            return operation()["status"]
        except ValueError:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(transition, operation)
            for operation in (
                lambda: service.approve_task(task_id, request),
                lambda: service.cancel_task(task_id),
            )
        ]
        outcomes = [f.result(timeout=5) for f in futures]
    assert "refused" in outcomes or "cancelled" in outcomes
    assert service.summary(task_id)["status"] in {"completed", "cancelled"}
    with pytest.raises(TaskStateError):
        service.approve_task(task_id, request)


def test_cost_acknowledgement_can_be_revised_before_generation(service):
    task_id, _ = planned(service)
    service.acknowledge_plan_cost(task_id, acknowledged_max_cny=1)
    assert service.plans.cost_acknowledgement(task_id)["acknowledged_max_cny"] == 1


def test_audit_failure_after_admission_never_dispatches_work(service, monkeypatch):
    task_id, plan = planned(service)
    ran = []

    def fail(*args, **kwargs):
        raise RuntimeError("injected audit-store failure")

    monkeypatch.setattr(service.ledger, "append_event", fail)
    monkeypatch.setattr(service, "_execute_queued", lambda *args: ran.append(True))
    with pytest.raises(ValueError):
        service.approve_task(task_id, decision(plan, action="approve"))
    assert service.summary(task_id)["status"] == "interrupted"
    assert not ran
    assert service.budget.get(task_id).reconciliation_required
