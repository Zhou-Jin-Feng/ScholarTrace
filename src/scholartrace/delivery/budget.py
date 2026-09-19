"""Whole-task budget reservation and settlement (T07).

Why a reservation exists at all: without one, a task's spend is only known after
it happens, so two tasks approved at once can each individually look affordable
and jointly exceed the ceiling. Reserving at approval makes the commitment
visible before any work starts.

Two failure modes drive the design, both of which are worse than doing nothing:

* reserve without settle — the ceiling stays consumed forever and later tasks
  are refused for capacity that nothing is using;
* settle without reserve — a task spends against a ceiling that was never
  checked, so the limit is not a limit.

So every reservation row carries its own settlement columns, written in a single
transaction, and an unsettled reservation on a terminal task is a *detectable*
fact that `reconcile_orphans` reports rather than something that silently rots.

A settlement computed from local counters is an accounting record, never a bill:
`is_actual_bill` stays false until reconciled against a real provider invoice.
"""

from __future__ import annotations

import math
import re
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from scholartrace.contracts import BudgetUsage
from scholartrace.delivery.transactions import transaction


class BudgetError(ValueError):
    """Base class for reservation failures, carrying a stable public code."""

    code = "budget_error"


class BudgetAlreadyReservedError(BudgetError):
    code = "budget_already_reserved"


class ReservationNotFoundError(BudgetError):
    code = "budget_reservation_not_found"


class ReservationCeilingExceededError(BudgetError):
    """The new reservation would push committed spend past the ceiling."""

    code = "budget_ceiling_exceeded"


def validate_budget_values(cny: float | Decimal, calls: int, seconds: int = 0) -> None:
    if isinstance(cny, bool) or not math.isfinite(cny) or cny < 0:
        raise BudgetError("CNY must be finite and non-negative")
    for value in (calls, seconds):
        if type(value) is not int or value < 0 or value > 2**63 - 1:
            raise BudgetError("counts and durations must be non-negative SQLite integers")


@dataclass(frozen=True)
class Reservation:
    task_id: str
    plan_version: int
    plan_digest: str
    reserved_cny: float
    reserved_api_calls: int
    reserved_wall_clock_seconds: int
    estimate_source: str
    reserved_at: str
    settled_at: str | None
    settled_cny: float | None
    settled_api_calls: int | None
    settled_reason: str | None
    is_actual_bill: bool
    reconciliation_required: bool
    recorded_cny: float
    recorded_api_calls: int

    @property
    def is_open(self) -> bool:
        return self.settled_at is None

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "plan_version": self.plan_version,
            "plan_digest": self.plan_digest,
            "reserved": {
                "cny": self.reserved_cny,
                "api_calls": self.reserved_api_calls,
                "wall_clock_seconds": self.reserved_wall_clock_seconds,
            },
            "estimate_source": self.estimate_source,
            "reserved_at": self.reserved_at,
            "settled": None
            if self.is_open
            else {
                "at": self.settled_at,
                "cny": self.settled_cny,
                "api_calls": self.settled_api_calls,
                "reason": self.settled_reason,
            },
            "state": (
                "reconciliation_required"
                if self.reconciliation_required
                else "open"
                if self.is_open
                else "settled"
            ),
            "recorded_usage": {"cny": self.recorded_cny, "api_calls": self.recorded_api_calls},
            "reconciliation_required": self.reconciliation_required,
            # Explicit so a caller cannot mistake a local tally for an invoice.
            "is_actual_bill": self.is_actual_bill,
        }


def _row_to_reservation(row: sqlite3.Row) -> Reservation:
    return Reservation(
        task_id=str(row["task_id"]),
        plan_version=int(row["plan_version"]),
        plan_digest=str(row["plan_digest"]),
        reserved_cny=float(row["reserved_cny"]),
        reserved_api_calls=int(row["reserved_api_calls"]),
        reserved_wall_clock_seconds=int(row["reserved_wall_clock_seconds"]),
        estimate_source=str(row["estimate_source"]),
        reserved_at=str(row["reserved_at"]),
        settled_at=None if row["settled_at"] is None else str(row["settled_at"]),
        settled_cny=None if row["settled_cny"] is None else float(row["settled_cny"]),
        settled_api_calls=(
            None if row["settled_api_calls"] is None else int(row["settled_api_calls"])
        ),
        settled_reason=None if row["settled_reason"] is None else str(row["settled_reason"]),
        is_actual_bill=bool(row["is_actual_bill"]),
        reconciliation_required=bool(row["reconciliation_required"]),
        recorded_cny=float(row["recorded_cny"]),
        recorded_api_calls=int(row["recorded_api_calls"]),
    )


class BudgetReservationStore:
    """Reserve at approval, settle at terminal state, reconcile after a crash."""

    def __init__(self, connection: sqlite3.Connection, lock: threading.RLock) -> None:
        self._connection = connection
        self._lock = lock

    def reserve(
        self,
        *,
        task_id: str,
        plan_version: int,
        plan_digest: str,
        reserved_cny: float,
        reserved_api_calls: int,
        reserved_wall_clock_seconds: int,
        estimate_source: str,
        ceiling_cny: float | None = None,
    ) -> Reservation:
        """Commit a reservation, or refuse. Never partially applied.

        Re-reserving the same task is refused rather than silently replacing the
        row: a second reservation would either double-commit the ceiling or lose
        the first commitment, and neither is safe to guess at.
        """

        validate_budget_values(reserved_cny, reserved_api_calls, reserved_wall_clock_seconds)
        if ceiling_cny is not None:
            validate_budget_values(ceiling_cny, 0)
        if type(plan_version) is not int or plan_version < 0:
            raise BudgetError("plan version must be a non-negative integer")
        with self._lock, transaction(self._connection):
            existing = self._connection.execute(
                "SELECT * FROM budget_reservations WHERE task_id = ?", (task_id,)
            ).fetchone()
            if existing is not None:
                raise BudgetAlreadyReservedError(
                    f"task {task_id} already holds a budget reservation"
                )
            if ceiling_cny is not None:
                committed = self._open_committed_cny()
                if Decimal(str(committed)) + Decimal(str(reserved_cny)) > Decimal(str(ceiling_cny)):
                    raise ReservationCeilingExceededError(
                        "reserving this task would exceed the approved ceiling "
                        f"({committed + reserved_cny} > {ceiling_cny} CNY committed)"
                    )
            now = datetime.now(UTC).isoformat()
            self._connection.execute(
                """
                INSERT INTO budget_reservations (
                    task_id, plan_version, plan_digest, reserved_cny,
                    reserved_api_calls, reserved_wall_clock_seconds,
                    estimate_source, reserved_at, is_actual_bill
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    task_id,
                    int(plan_version),
                    plan_digest,
                    float(reserved_cny),
                    int(reserved_api_calls),
                    int(reserved_wall_clock_seconds),
                    estimate_source,
                    now,
                ),
            )
        result = self.get(task_id)
        assert result is not None  # just inserted inside the transaction
        return result

    def bind_authorized_plan(
        self, *, task_id: str, policy_sha256: str, plan_version: int, plan_digest: str,
        expected_cny: Decimal, expected_api_calls: int, expected_wall_clock_seconds: int,
    ) -> Reservation:
        """Bind Gate B to Gate A's existing capacity without releasing or refilling it."""
        validate_budget_values(expected_cny, expected_api_calls, expected_wall_clock_seconds)
        if (type(plan_version) is not int or plan_version < 1
                or re.fullmatch(r"[a-f0-9]{64}", policy_sha256) is None
                or re.fullmatch(r"[a-f0-9]{64}", plan_digest) is None):
            raise BudgetError("approved plan and policy identities must be explicit")
        with self._lock, transaction(self._connection):
            row = self._connection.execute(
                "SELECT * FROM budget_reservations WHERE task_id=?", (task_id,),
            ).fetchone()
            if row is None:
                raise ReservationNotFoundError("Gate A task reservation is missing")
            if (row["settled_at"] is not None or row["reconciliation_required"]
                    or row["estimate_source"] != "authorized:" + policy_sha256
                    or Decimal(str(row["reserved_cny"])) != expected_cny
                    or row["reserved_api_calls"] != expected_api_calls
                    or row["reserved_wall_clock_seconds"] != expected_wall_clock_seconds):
                raise BudgetError("task reservation differs from original authorization")
            if row["plan_version"] == plan_version and row["plan_digest"] == plan_digest:
                return _row_to_reservation(row)
            if row["plan_version"] >= plan_version:
                raise BudgetError("approved plan version cannot move backwards or change in place")
            self._connection.execute(
                "UPDATE budget_reservations SET plan_version=?,plan_digest=? WHERE task_id=?",
                (plan_version, plan_digest, task_id),
            )
            updated = self.get(task_id)
            assert updated is not None
            return updated

    def settle(
        self,
        *,
        task_id: str,
        settled_cny: float,
        settled_api_calls: int,
        reason: str,
        is_actual_bill: bool = False,
    ) -> Reservation:
        """Close a reservation exactly once.

        Idempotent by design: a terminal path may run twice (retry, restart), and
        settling twice would either double-count spend or overwrite the first
        settlement's reason. A repeat call returns the existing settlement
        unchanged instead.
        """

        validate_budget_values(settled_cny, settled_api_calls)
        with self._lock, transaction(self._connection):
            row = self._connection.execute(
                "SELECT * FROM budget_reservations WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise ReservationNotFoundError(
                    f"task {task_id} has no budget reservation to settle"
                )
            if row["settled_at"] is not None:
                if (
                    float(row["settled_cny"]) != settled_cny
                    or int(row["settled_api_calls"]) != settled_api_calls
                    or bool(row["is_actual_bill"]) != is_actual_bill
                ):
                    raise BudgetError("settlement conflicts with the recorded usage")
                return _row_to_reservation(row)
            self._connection.execute(
                """
                UPDATE budget_reservations
                   SET settled_at = ?, settled_cny = ?, settled_api_calls = ?,
                       settled_reason = ?, is_actual_bill = ?, reconciliation_required = 0,
                       recorded_cny = ?, recorded_api_calls = ?
                 WHERE task_id = ? AND settled_at IS NULL
                """,
                (
                    datetime.now(UTC).isoformat(),
                    float(settled_cny),
                    int(settled_api_calls),
                    reason,
                    1 if is_actual_bill else 0,
                    settled_cny,
                    settled_api_calls,
                    task_id,
                ),
            )
        result = self.get(task_id)
        assert result is not None
        return result

    def release(self, task_id: str, *, reason: str) -> bool:
        """Delete a reservation for work that never started.

        Distinct from `settle` on purpose. Settling records "this task ran and
        consumed X"; releasing records "this commitment should never have been
        held". The queue-full path needs the latter: the task goes back to
        `waiting_approval` for a genuine retry, and `reserve` refuses when any
        row exists — settled or not — so a settled row would permanently block
        re-approval of a task that never ran.

        Returns whether a row was removed, so a caller can tell a real release
        from a no-op instead of assuming.
        """

        with self._lock, transaction(self._connection):
            row = self._connection.execute(
                "SELECT settled_at, reconciliation_required FROM budget_reservations "
                "WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return False
            if row["settled_at"] is not None or row["reconciliation_required"]:
                # Already settled means the task did run; deleting that would
                # erase a spend record. Refuse rather than lose the history.
                return False
            self._connection.execute(
                "DELETE FROM budget_reservations WHERE task_id = ? AND settled_at IS NULL",
                (task_id,),
            )
        return True

    def get(self, task_id: str) -> Reservation | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM budget_reservations WHERE task_id = ?", (task_id,)
            ).fetchone()
        return None if row is None else _row_to_reservation(row)

    def open_reservations(self) -> list[Reservation]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM budget_reservations WHERE settled_at IS NULL ORDER BY reserved_at"
            ).fetchall()
        return [_row_to_reservation(row) for row in rows]

    def _open_committed_cny(self) -> float:
        rows = self._connection.execute(
            "SELECT reserved_cny, recorded_cny FROM budget_reservations WHERE settled_at IS NULL"
        ).fetchall()
        return float(
            sum((max(Decimal(str(row[0])), Decimal(str(row[1]))) for row in rows), Decimal(0))
        )

    def committed_cny(self) -> float:
        """Currently reserved-but-unsettled CNY across all tasks."""

        with self._lock:
            return self._open_committed_cny()

    def mark_unconfirmed(self, task_id: str, usage: BudgetUsage) -> Reservation:
        """Keep exposure held; local counters cannot prove an external call did not bill."""
        with self._lock, transaction(self._connection):
            self._connection.execute(
                "UPDATE budget_reservations SET reconciliation_required = 1, "
                "recorded_cny = MAX(recorded_cny, ?), "
                "recorded_api_calls = MAX(recorded_api_calls, ?) "
                "WHERE task_id = ? AND settled_at IS NULL",
                (usage.external_cost_cny, usage.api_calls, task_id),
            )
            result = self.get(task_id)
            if result is None:
                raise ReservationNotFoundError(task_id)
            return result

    def reconcile_orphans(
        self,
        *,
        terminal_task_ids: set[str],
        measured_usage: Callable[[str], BudgetUsage] | None = None,
        known_complete_task_ids: set[str] | None = None,
    ) -> list[Reservation]:
        """Settle only proven complete measurements; retain all unknown exposure."""
        reconciled = []
        for reservation in self.open_reservations():
            if reservation.task_id not in terminal_task_ids:
                continue
            usage = measured_usage(reservation.task_id) if measured_usage else BudgetUsage()
            if measured_usage and reservation.task_id in (known_complete_task_ids or set()):
                result = self.settle(
                    task_id=reservation.task_id,
                    settled_cny=usage.external_cost_cny,
                    settled_api_calls=usage.api_calls,
                    reason="reconciled from complete local ledger",
                )
            else:
                result = self.mark_unconfirmed(reservation.task_id, usage)
            reconciled.append(result)
        return reconciled

    def check_usage(self, task_id: str, projected: BudgetUsage) -> None:
        """Pre-dispatch guard, not a charge or an atomic per-call reservation."""
        with self._lock:
            reservation = self.get(task_id)
            if (
                reservation is None
                or not reservation.is_open
                or reservation.reconciliation_required
            ):
                raise BudgetError("no confirmed open reservation for this task")
            if (
                projected.external_cost_cny > reservation.reserved_cny
                or projected.api_calls > reservation.reserved_api_calls
                or projected.elapsed_seconds > reservation.reserved_wall_clock_seconds
            ):
                raise ReservationCeilingExceededError("projected usage exceeds the task budget")
