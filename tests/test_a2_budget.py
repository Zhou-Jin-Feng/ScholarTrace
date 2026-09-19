"""T07 whole-task budget: reserve, settle, and survive a crash in between.

Two failure modes are worse than having no reservation at all, and each has a
test here:

* reserve without settle — the ceiling stays committed forever, so later tasks
  are refused for capacity nothing is using;
* settle without reserve — spend lands against a ceiling never checked.

The third case is the queue-full retry: a reservation for work that never
started must be *released*, not settled, or the task can never be re-approved.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from scholartrace.delivery.budget import (
    BudgetAlreadyReservedError,
    BudgetReservationStore,
    ReservationCeilingExceededError,
    ReservationNotFoundError,
)
from scholartrace.delivery.migrations import CURRENT_SCHEMA_VERSION
from scholartrace.delivery.models import (
    ApprovalRequest,
    DemoMode,
    TaskCreateRequest,
    TaskStatus,
)
from scholartrace.delivery.service import M6TaskService
from scholartrace.delivery.store import DeliveryStore

ROOT = Path(__file__).resolve().parents[1]


def _store(tmp_path: Path) -> BudgetReservationStore:
    # Go through DeliveryStore so the schema is built the way production builds
    # it: the v1->v2 step operates on the `tasks` table and cannot run on a
    # bare database.
    delivery = DeliveryStore(tmp_path / "budget.sqlite")
    return BudgetReservationStore(delivery.connection, delivery._lock)


def _reserve(store: BudgetReservationStore, task_id: str, cny: float, **kwargs: object):
    return store.reserve(
        task_id=task_id,
        plan_version=1,
        plan_digest="d" * 64,
        reserved_cny=cny,
        reserved_api_calls=10,
        reserved_wall_clock_seconds=600,
        estimate_source="fixture",
        **kwargs,  # type: ignore[arg-type]
    )


def test_schema_v3_adds_reservations_without_touching_effects(tmp_path: Path) -> None:
    """Reservations live apart from the ledger's actual-usage table."""

    delivery = DeliveryStore(tmp_path / "s.sqlite")
    assert delivery.schema_version == CURRENT_SCHEMA_VERSION
    tables = {
        row[0]
        for row in delivery.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "budget_reservations" in tables
    # A reservation must never be mistaken for a recorded charge: actual usage
    # lives in the ledger's own database, not alongside reservations.
    assert "budget_effects" not in tables


def test_reserve_then_settle_is_paired_and_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    reservation = _reserve(store, "task:1", 12.5)
    assert reservation.is_open
    assert store.committed_cny() == pytest.approx(12.5)
    # A reservation is not a bill, even once settled from local counters.
    assert reservation.is_actual_bill is False

    settled = store.settle(task_id="task:1", settled_cny=3.25, settled_api_calls=4, reason="done")
    assert not settled.is_open
    assert settled.settled_cny == pytest.approx(3.25)
    # Settling frees the commitment so later tasks are not refused for nothing.
    assert store.committed_cny() == pytest.approx(0.0)

    # A terminal path may run twice; the second settle must not double-count
    # nor overwrite the first reason.
    again = store.settle(
        task_id="task:1", settled_cny=3.25, settled_api_calls=4, reason="second call"
    )
    assert again.settled_cny == pytest.approx(3.25)
    assert again.settled_reason == "done"


def test_settle_without_reserve_is_refused(tmp_path: Path) -> None:
    """Spending against a ceiling that was never checked must not be silent."""

    store = _store(tmp_path)
    with pytest.raises(ReservationNotFoundError):
        store.settle(task_id="ghost", settled_cny=5.0, settled_api_calls=1, reason="x")


def test_double_reserve_is_refused(tmp_path: Path) -> None:
    """Guessing between double-committing and losing the first is unsafe."""

    store = _store(tmp_path)
    _reserve(store, "task:1", 5.0)
    with pytest.raises(BudgetAlreadyReservedError):
        _reserve(store, "task:1", 5.0)
    assert store.committed_cny() == pytest.approx(5.0)


def test_ceiling_counts_open_reservations_across_tasks(tmp_path: Path) -> None:
    """Two individually-affordable tasks must not jointly exceed the ceiling."""

    store = _store(tmp_path)
    _reserve(store, "task:1", 60.0, ceiling_cny=100.0)
    with pytest.raises(ReservationCeilingExceededError):
        _reserve(store, "task:2", 60.0, ceiling_cny=100.0)

    # Settling the first frees room for the second.
    store.settle(task_id="task:1", settled_cny=1.0, settled_api_calls=1, reason="done")
    accepted = _reserve(store, "task:2", 60.0, ceiling_cny=100.0)
    assert accepted.is_open


def test_release_removes_a_commitment_for_work_that_never_started(tmp_path: Path) -> None:
    """The queue-full retry path: release, not settle.

    `reserve` refuses when any row exists, so a settled row would permanently
    block re-approval of a task that never ran.
    """

    store = _store(tmp_path)
    _reserve(store, "task:1", 20.0)
    assert store.release("task:1", reason="queue full") is True
    assert store.committed_cny() == pytest.approx(0.0)
    # Re-approval must now succeed.
    assert _reserve(store, "task:1", 20.0).is_open


def test_release_refuses_to_erase_a_settled_spend_record(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _reserve(store, "task:1", 20.0)
    store.settle(task_id="task:1", settled_cny=7.0, settled_api_calls=2, reason="ran")
    assert store.release("task:1", reason="should not erase history") is False
    assert store.get("task:1") is not None


def test_reconcile_settles_only_orphans_of_terminal_tasks(tmp_path: Path) -> None:
    """Crash between reserve and settle: detectable, not a silent leak."""

    store = _store(tmp_path)
    _reserve(store, "task:done", 30.0)
    _reserve(store, "task:running", 30.0)

    reconciled = store.reconcile_orphans(terminal_task_ids={"task:done"})
    assert [item.task_id for item in reconciled] == ["task:done"]
    # A still-running task keeps its commitment; freeing it would let a live
    # task spend against a ceiling nobody is holding.
    assert store.get("task:running") is not None
    assert store.get("task:running").is_open is True  # type: ignore[union-attr]
    assert store.committed_cny() == pytest.approx(60.0)
    # Missing measurements do not prove that external spend was zero.
    assert reconciled[0].settled_cny is None
    assert reconciled[0].reconciliation_required


def test_service_reserves_on_approval_and_settles_at_terminal(tmp_path: Path) -> None:
    """End-to-end through the service, including event ordering."""

    service = M6TaskService(root=ROOT, data_dir=tmp_path)
    try:
        created = service.create_task(
            TaskCreateRequest(
                title="T07",
                question="Is the reservation paired with a settlement?",
                demo_mode=DemoMode.SUCCESS,
            )
        )
        task_id = str(created["task_id"])
        summary = service.approve_task(task_id, ApprovalRequest(action="approve", reason="t07"))
        assert summary["status"] in {"queued", "running", "completed", "degraded"}

        for _ in range(120):
            status = str(service.summary(task_id)["status"])
            if status in {"completed", "degraded", "failed", "cancelled"}:
                break
            time.sleep(0.25)

        reservation = service.budget.get(task_id)
        assert reservation is not None
        assert not reservation.is_open, "terminal task must not hold an open reservation"
        assert store_committed_is_zero(service)

        kinds = [str(event["kind"]) for event in service.events(task_id)]
        assert "budget_reserved" in kinds
        assert "budget_settled" in kinds
        # `exports_ready` closes the SSE stream, so settlement must precede it
        # or the client never receives it.
        assert kinds.index("budget_settled") < kinds.index("exports_ready")

        report = service.budget_report(task_id)
        # A demo run makes no paid calls, and nothing here is a real bill.
        assert report["is_actual_bill"] is False
        assert report["reservation"]["state"] == "settled"
    finally:
        service.close()


def store_committed_is_zero(service: M6TaskService) -> bool:
    return abs(service.budget.committed_cny()) < 1e-9


def test_startup_reconciliation_closes_a_crashed_reservation(tmp_path: Path) -> None:
    """Simulate a crash: reserve, mark terminal, drop the process, restart."""

    data_dir = tmp_path / "shared"
    service = M6TaskService(root=ROOT, data_dir=data_dir)
    try:
        created = service.create_task(
            TaskCreateRequest(
                title="T07 crash",
                question="Is an orphaned reservation reconciled at startup?",
                demo_mode=DemoMode.SUCCESS,
            )
        )
        task_id = str(created["task_id"])
        service.budget.reserve(
            task_id=task_id,
            plan_version=1,
            plan_digest="e" * 64,
            reserved_cny=42.0,
            reserved_api_calls=5,
            reserved_wall_clock_seconds=300,
            estimate_source="fixture",
        )
        # Terminal state, but the settlement never happened: the process died.
        service.store.update_task(task_id, status=TaskStatus.FAILED)
        assert service.budget.committed_cny() == pytest.approx(42.0)
    finally:
        service.close()

    restarted = M6TaskService(root=ROOT, data_dir=data_dir)
    try:
        assert restarted.budget.committed_cny() == pytest.approx(0.0)
        reservation = restarted.budget.get(task_id)
        assert reservation is not None and not reservation.is_open
        # The fix is auditable, not silent.
        assert "budget_reservation_reconciled" in [
            str(event["kind"]) for event in restarted.events(task_id)
        ]
    finally:
        restarted.close()
