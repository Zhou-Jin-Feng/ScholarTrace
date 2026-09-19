"""Two-stage plan approval gate: cost acknowledgement, then version-bound approval."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from scholartrace.delivery.models import DemoMode
from scholartrace.delivery.plans import (
    BudgetPlan,
    PaperCountOutOfRangeError,
    PlanApprovalState,
    PlanCostNotAcknowledgedError,
    PlanNotFoundError,
    PlanOrigin,
    PlanStore,
    PlanVersionStaleError,
    SourceScope,
    compute_plan_digest,
)
from scholartrace.delivery.store import DeliveryStore

SCOPE = SourceScope(providers=("arxiv", "openalex"), year_from=2020, year_to=2025)
BUDGET = BudgetPlan(
    max_cny=0.0,
    max_api_calls=0,
    max_wall_clock_seconds=1800,
    estimate_source="fixture",
)


def _store(tmp_path: Path) -> tuple[DeliveryStore, PlanStore]:
    delivery = DeliveryStore(tmp_path / "tasks.sqlite")
    delivery.create_task(
        task_id="task:a2:1",
        thread_id="thread:a2:1",
        title="T",
        question="How is corrective retrieval triggered?",
        demo_mode=DemoMode.SUCCESS,
    )
    return delivery, PlanStore(delivery.connection, threading.RLock())


def _save(plans: PlanStore, *, questions: tuple[str, ...] = ("q1",), **kwargs: object):
    return plans.save_plan(
        task_id="task:a2:1",
        generated_by=PlanOrigin.FIXTURE,
        sub_questions=questions,
        source_scope=kwargs.pop("scope", SCOPE),  # type: ignore[arg-type]
        exclusions=(),
        budget_plan=BUDGET,
        require_acknowledgement=False,
        **kwargs,  # type: ignore[arg-type]
    )


def test_generation_refuses_before_cost_is_acknowledged(tmp_path: Path) -> None:
    delivery, plans = _store(tmp_path)
    try:
        with pytest.raises(PlanCostNotAcknowledgedError):
            plans.save_plan(
                task_id="task:a2:1",
                generated_by=PlanOrigin.API_STRONG,
                sub_questions=("q1",),
                source_scope=SCOPE,
                exclusions=(),
                budget_plan=BUDGET,
                estimated_cost_cny=0.5,
            )
        # Gate A satisfied, but only up to the ceiling the user actually accepted.
        plans.acknowledge_plan_cost(
            task_id="task:a2:1", acknowledged_max_cny=0.2, estimate_source="price-list"
        )
        with pytest.raises(PlanCostNotAcknowledgedError):
            plans.save_plan(
                task_id="task:a2:1",
                generated_by=PlanOrigin.API_STRONG,
                sub_questions=("q1",),
                source_scope=SCOPE,
                exclusions=(),
                budget_plan=BUDGET,
                estimated_cost_cny=0.5,
            )
        record = plans.save_plan(
            task_id="task:a2:1",
            generated_by=PlanOrigin.API_STRONG,
            sub_questions=("q1",),
            source_scope=SCOPE,
            exclusions=(),
            budget_plan=BUDGET,
            estimated_cost_cny=0.2,
        )
        assert record.plan_version == 1
        assert record.approval_state is PlanApprovalState.WAITING_APPROVAL
    finally:
        delivery.close()


def test_no_plan_yields_not_found_rather_than_an_empty_plan(tmp_path: Path) -> None:
    delivery, plans = _store(tmp_path)
    try:
        with pytest.raises(PlanNotFoundError):
            plans.current_plan("task:a2:1")
    finally:
        delivery.close()


def test_stale_version_or_digest_refuses_approval(tmp_path: Path) -> None:
    delivery, plans = _store(tmp_path)
    try:
        first = _save(plans)
        second = _save(plans, questions=("q1", "q2"))
        assert second.plan_version == 2
        assert second.supersedes_version == 1

        # Approving the version the user read earlier must refuse, not execute.
        with pytest.raises(PlanVersionStaleError):
            plans.verify_approval_target(
                task_id="task:a2:1",
                plan_version=first.plan_version,
                plan_digest=first.plan_digest,
            )
        # Right version number, wrong content: still refuses.
        with pytest.raises(PlanVersionStaleError):
            plans.verify_approval_target(
                task_id="task:a2:1",
                plan_version=second.plan_version,
                plan_digest=first.plan_digest,
            )
        target = plans.verify_approval_target(
            task_id="task:a2:1",
            plan_version=second.plan_version,
            plan_digest=second.plan_digest,
        )
        assert target.plan_version == 2
    finally:
        delivery.close()


def test_superseded_plan_cannot_be_approved_after_regeneration(tmp_path: Path) -> None:
    delivery, plans = _store(tmp_path)
    try:
        first = _save(plans)
        _save(plans, questions=("q1", "q2"))
        versions = plans.plan_versions("task:a2:1")
        assert [v.approval_state for v in versions] == [
            PlanApprovalState.SUPERSEDED,
            PlanApprovalState.WAITING_APPROVAL,
        ]
        assert first.plan_digest != versions[1].plan_digest
    finally:
        delivery.close()


def test_modify_supersedes_and_never_approves(tmp_path: Path) -> None:
    delivery, plans = _store(tmp_path)
    try:
        record = _save(plans)
        decision = plans.record_decision(
            task_id="task:a2:1",
            plan_version=record.plan_version,
            plan_digest=record.plan_digest,
            action="modify",
            reason="narrow to four papers",
        )
        assert decision["action"] == "modify"
        # A modify must not leave anything approved: nothing may execute yet.
        assert plans.current_plan("task:a2:1").approval_state is PlanApprovalState.SUPERSEDED
        with pytest.raises(PlanVersionStaleError):
            plans.verify_approval_target(
                task_id="task:a2:1",
                plan_version=record.plan_version,
                plan_digest=record.plan_digest,
            )
    finally:
        delivery.close()


def test_reject_marks_rejected_and_leaves_no_approved_version(tmp_path: Path) -> None:
    delivery, plans = _store(tmp_path)
    try:
        record = _save(plans)
        plans.record_decision(
            task_id="task:a2:1",
            plan_version=record.plan_version,
            plan_digest=record.plan_digest,
            action="reject",
            reason="scope too broad",
        )
        assert plans.current_plan("task:a2:1").approval_state is PlanApprovalState.REJECTED
    finally:
        delivery.close()


def test_repeated_idempotency_key_replays_instead_of_deciding_twice(tmp_path: Path) -> None:
    delivery, plans = _store(tmp_path)
    try:
        record = _save(plans)
        first = plans.record_decision(
            task_id="task:a2:1",
            plan_version=record.plan_version,
            plan_digest=record.plan_digest,
            action="approve",
            reason=None,
            idempotency_key="approve-1",
        )
        second = plans.record_decision(
            task_id="task:a2:1",
            plan_version=record.plan_version,
            plan_digest=record.plan_digest,
            action="approve",
            reason=None,
            idempotency_key="approve-1",
        )
        assert first["replayed"] is False
        assert second["replayed"] is True
        assert second["approval_id"] == first["approval_id"]
        count = delivery.connection.execute(
            "SELECT COUNT(*) FROM plan_approvals WHERE task_id = ?", ("task:a2:1",)
        ).fetchone()[0]
        assert count == 1
    finally:
        delivery.close()


@pytest.mark.parametrize(
    "min_papers,max_papers",
    [(2, 5), (3, 6), (1, 2), (4, 3)],
)
def test_paper_count_outside_the_frozen_m2_range_is_refused(
    tmp_path: Path, min_papers: int, max_papers: int
) -> None:
    delivery, plans = _store(tmp_path)
    try:
        scope = SourceScope(
            providers=("arxiv",),
            year_from=2020,
            year_to=2025,
            min_papers=min_papers,
            max_papers=max_papers,
        )
        # Refused outright rather than silently clamped into the frozen range.
        with pytest.raises(PaperCountOutOfRangeError):
            _save(plans, scope=scope)
    finally:
        delivery.close()


def test_digest_ignores_version_and_timestamp_but_tracks_content(tmp_path: Path) -> None:
    base = compute_plan_digest(
        sub_questions=("q1",), source_scope=SCOPE, exclusions=(), budget_plan=BUDGET
    )
    same = compute_plan_digest(
        sub_questions=("q1",), source_scope=SCOPE, exclusions=(), budget_plan=BUDGET
    )
    changed = compute_plan_digest(
        sub_questions=("q1",), source_scope=SCOPE, exclusions=("non-English",), budget_plan=BUDGET
    )
    assert base == same
    assert base != changed


def test_approval_records_survive_reopen(tmp_path: Path) -> None:
    delivery, plans = _store(tmp_path)
    record = _save(plans)
    plans.record_decision(
        task_id="task:a2:1",
        plan_version=record.plan_version,
        plan_digest=record.plan_digest,
        action="approve",
        reason=None,
    )
    delivery.close()

    reopened = DeliveryStore(tmp_path / "tasks.sqlite")
    try:
        again = PlanStore(reopened.connection, threading.RLock())
        assert again.current_plan("task:a2:1").approval_state is PlanApprovalState.APPROVED
        assert again.plan_versions("task:a2:1")[0].plan_digest == record.plan_digest
    finally:
        reopened.close()


def test_plan_rows_are_scoped_per_task(tmp_path: Path) -> None:
    delivery, plans = _store(tmp_path)
    try:
        delivery.create_task(
            task_id="task:a2:2",
            thread_id="thread:a2:2",
            title="Other",
            question="Unrelated question",
            demo_mode=DemoMode.SUCCESS,
        )
        _save(plans)
        with pytest.raises(PlanNotFoundError):
            plans.current_plan("task:a2:2")
    finally:
        delivery.close()


def test_unique_index_blocks_duplicate_approval_keys(tmp_path: Path) -> None:
    delivery, plans = _store(tmp_path)
    try:
        record = _save(plans)
        plans.record_decision(
            task_id="task:a2:1",
            plan_version=record.plan_version,
            plan_digest=record.plan_digest,
            action="approve",
            reason=None,
            idempotency_key="dup",
        )
        # Same key against a different target is a conflict, not a silent replay.
        with pytest.raises((sqlite3.IntegrityError, ValueError)):
            plans.record_decision(
                task_id="task:a2:1",
                plan_version=99,
                plan_digest="deadbeef",
                action="approve",
                reason=None,
                idempotency_key="dup",
            )
    finally:
        delivery.close()
