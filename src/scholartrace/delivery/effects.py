"""Durable per-operation reservations and replay, without an exactly-once claim.

Commit the intent before dispatch. Uncertain results retain their ceiling and
are never automatically reissued. Only an explicit pre-dispatch failure is
retryable; successful results and measured usage commit together.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from scholartrace.contracts import BudgetUsage
from scholartrace.delivery.authorization import (
    AuthorizationError,
    CallContext,
    CallUsage,
    PhaseGrant,
    RuntimePolicy,
    TaskGrant,
    activate_phase_grant,
    active_task,
    check_context_capacity,
    check_operation_attempt,
    save_phase_grant,
    save_task_grant,
    validate_call_context,
)
from scholartrace.delivery.budget import BudgetError, validate_budget_values
from scholartrace.delivery.effect_schema import migrate_effect_schema
from scholartrace.restore_fence import is_restore_fenced
from scholartrace.workflow.storage import RuntimeLedger


class EffectUncertainError(RuntimeError):
    """The operation may have happened; reconciliation is required before replay."""


class MeasuredUsageError(EffectUncertainError):
    """A response confirms usage but violates an approved resource ceiling."""

    def __init__(self, cost: float | Decimal, usage: CallUsage | None = None) -> None:
        validate_budget_values(cost, 0)
        self.cost = cost
        self.usage = usage
        super().__init__("confirmed usage exceeded the approved resource ceiling")


class EffectConflictError(ValueError):
    """A stable effect key or task budget was reused with a different contract."""


class BeforeDispatchError(RuntimeError):
    """An adapter positively knows that no external operation was dispatched."""


class EffectCancelledError(RuntimeError):
    """No operation was started because cancellation was already requested."""


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


class EffectJournal:
    def __init__(self, path: Path, *, projection_ledger: RuntimeLedger | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._projection_ledger = projection_ledger
        self._path = path.absolute()
        self._resolved_path = path.resolve()
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        try:
            migrate_effect_schema(self.db)
        except BaseException:
            self.db.close()
            raise

    def _require_unfenced_restore(self) -> None:
        try:
            fenced = is_restore_fenced(self._path) or is_restore_fenced(self._resolved_path)
        except OSError as exc:
            raise AuthorizationError(
                "restore safety cannot be verified; reconciliation required"
            ) from exc
        if fenced:
            raise AuthorizationError(
                "restored data requires reconciliation before dispatch or activation"
            )

    def authorize_task(self, grant: TaskGrant, *, now: datetime | None = None) -> None:
        """Persist an immutable task envelope; phase activation remains separate."""
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                save_task_grant(self.db, grant, now=now or datetime.now(UTC))
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self.db.close()

    def prepare_phase(self, grant: PhaseGrant, *, now: datetime | None = None) -> None:
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                save_phase_grant(self.db, grant, now=now or datetime.now(UTC))
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def approved_policy(self, task_id: str) -> RuntimePolicy:
        """Return a validated immutable snapshot; dispatch still needs atomic admission."""
        with self._lock:
            task = active_task(self.db, task_id, datetime.now(UTC))
            return RuntimePolicy.model_validate_json(task["policy_json"])

    def approved_limits(self, task_id: str) -> tuple[Decimal, int]:
        with self._lock:
            task = active_task(self.db, task_id, datetime.now(UTC))
            return Decimal(task["max_cny"]), int(task["max_calls"])

    def approved_deadline(self, task_id: str) -> datetime:
        with self._lock:
            task = active_task(self.db, task_id, datetime.now(UTC))
            return datetime.fromisoformat(task["deadline_at"])

    def active_phase(self, task_id: str, phase: str) -> PhaseGrant:
        with self._lock:
            active_task(self.db, task_id, datetime.now(UTC))
            row = self.db.execute(
                "SELECT * FROM phase_authorizations WHERE task_id=? AND phase=? "
                "AND state='active'", (task_id, phase),
            ).fetchone()
            if row is None:
                raise AuthorizationError("an active phase authorization is required")
            return PhaseGrant.model_validate({key: row[key] for key in PhaseGrant.model_fields})

    def reconcile_stage_resume(
        self, task_id: str, *, plan_version: int, plan_digest: str,
    ) -> None:
        """Explicitly recover orchestration only, never unknown external effects.

        This is restricted to the metered production assembly. A stage is local
        orchestration with zero independent exposure; its children carry all
        actual requests and must be durably confirmed before stage replay.
        """
        ledger = self._projection_ledger
        if ledger is None:
            raise AuthorizationError("recovery requires the bound projection ledger")
        self._require_unfenced_restore()
        projection = self.project_usage(task_id, ledger)
        if projection["pending_effects"]:
            raise AuthorizationError("usage projection requires reconciliation before recovery")
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                phase = self.active_phase(task_id, "execution")
                if phase.plan_version != plan_version or phase.plan_digest != plan_digest:
                    raise AuthorizationError("recovery does not match the active approved plan")
                rows = self.db.execute(
                    "SELECT e.*,c.call_kind,c.authorization_id FROM effects e "
                    "LEFT JOIN effect_contexts c USING(task_id,effect_key) "
                    "WHERE e.task_id=? AND e.state IN ('pending','unknown')", (task_id,),
                ).fetchall()
                for row in rows:
                    if (row["call_kind"] != "stage"
                            or row["authorization_id"] != phase.authorization_id
                            or Decimal(row["reserve_cny"]) != 0 or row["reserve_calls"] != 0
                            or Decimal(row["measured_cny"] or "0") != 0):
                        raise AuthorizationError("unknown external exposure cannot be resumed")
                    if row["attempts"] >= 2:
                        raise AuthorizationError("bounded stage recovery limit reached")
                self.require_projection_synced(task_id)
                for row in rows:
                    self.db.execute(
                        "UPDATE effects SET state='retryable' WHERE task_id=? AND effect_key=?",
                        (task_id, row["effect_key"]),
                    )
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def has_authorization(self, task_id: str) -> bool:
        with self._lock:
            return self.db.execute(
                "SELECT 1 FROM task_authorizations WHERE task_id=?", (task_id,),
            ).fetchone() is not None

    def revoke_phase(self, task_id: str, phase: str) -> None:
        if phase not in {"planning", "execution"}:
            raise AuthorizationError("unsupported phase")
        with self._lock, self.db:
            self.db.execute(
                "UPDATE phase_authorizations SET state='revoked' "
                "WHERE task_id=? AND phase=? AND state IN ('pending','active')", (task_id, phase),
            )

    def activate_phase(self, grant: PhaseGrant, *, now: datetime | None = None) -> None:
        """Internal service boundary: caller must first persist and audit approval."""
        with self._lock:
            self._require_unfenced_restore()
            self.db.execute("BEGIN IMMEDIATE")
            try:
                activate_phase_grant(self.db, grant, now=now or datetime.now(UTC))
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def revoke_task(self, task_id: str, *, now: datetime | None = None) -> None:
        """Retain all cost and unknown exposure while forbidding new dispatch."""
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                moment = now or datetime.now(UTC)
                if moment.tzinfo is None:
                    raise AuthorizationError("revocation requires a timezone-aware clock")
                row = self.db.execute(
                    "SELECT state FROM task_authorizations WHERE task_id=?", (task_id,),
                ).fetchone()
                if row is None:
                    raise AuthorizationError("task authorization does not exist")
                if row["state"] == "active":
                    self.db.execute(
                        "UPDATE task_authorizations SET state='revoked',revoked_at=? "
                        "WHERE task_id=?", (moment.astimezone(UTC).isoformat(), task_id),
                    )
                    self.db.execute(
                        "UPDATE phase_authorizations SET state='revoked' "
                        "WHERE task_id=? AND state IN ('pending','active')", (task_id,),
                    )
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    @staticmethod
    def _projection_record(row: sqlite3.Row, task_id: str) -> tuple[str, BudgetUsage]:
        if row["usage_json"] is None:
            raise AuthorizationError("journal usage projection is incomplete")
        record = CallUsage.model_validate_json(row["usage_json"])
        model = row["call_kind"] in {"remote_model", "local_model"}
        if model and (record.input_tokens is None or record.output_tokens is None):
            raise AuthorizationError("journal usage projection lacks confirmed tokens")
        delta = BudgetUsage(
            llm_input_tokens=record.input_tokens or 0,
            llm_output_tokens=record.output_tokens or 0,
            model_calls=int(model), api_calls=row["reserve_calls"],
            external_cost_cny=float(Decimal(row["measured_cny"])),
        )
        identity = hashlib.sha256(_json((task_id, row["effect_key"])).encode()).hexdigest()
        return "journal:" + identity, delta

    def require_projection_synced(self, task_id: str) -> None:
        """Stop new production operations on durable journal/ledger disagreement.

        No new schema or control file is needed: both authoritative measurements
        and their exact idempotent projections already survive a process restart.
        Reconciliation is explicit through project_usage, not admission.
        """
        ledger = self._projection_ledger
        if ledger is None:
            return  # Legacy and standalone component callers; production binds a ledger.
        with self._lock:
            rows = self.db.execute(
                "SELECT e.*,c.call_kind,c.usage_json FROM effects e "
                "JOIN effect_contexts c USING(task_id,effect_key) "
                "WHERE e.task_id=? AND e.state='completed' AND c.call_kind!='stage'",
                (task_id,),
            ).fetchall()
            try:
                for row in rows:
                    key, delta = self._projection_record(row, task_id)
                    if not ledger.confirms_projected_usage(
                        effect_key=key, task_id=task_id, delta=delta,
                    ):
                        raise AuthorizationError("journal usage projection requires reconciliation")
            except Exception as exc:
                raise AuthorizationError(
                    "journal usage projection requires reconciliation"
                ) from exc

    async def _dispatch_authorized(
        self, *, task_id: str, context: CallContext,
        operation: Callable[[], Awaitable[dict[str, Any]]],
        cancel_event: threading.Event, reserve_cny: Decimal, reserve_calls: int,
        now: datetime | None,
    ) -> dict[str, Any]:
        """Recheck at dispatch and bound an in-flight coroutine by the original grant.

        The journal cannot retract an external effect already sent. Cancellation
        after starting therefore retains unknown exposure, including phase revocation.
        """
        started = time.monotonic()

        def verify() -> None:
            if cancel_event.is_set():
                raise EffectCancelledError("operation cancellation requested")
            moment = (datetime.now(UTC) if now is None else
                      now + timedelta(seconds=time.monotonic() - started))
            with self._lock:
                self._require_unfenced_restore()
                self.require_projection_synced(task_id)
                validate_call_context(
                    self.db, task_id, context, now=moment,
                    reserve_cny=reserve_cny, reserve_calls=reserve_calls,
                )

        try:
            verify()
        except (AuthorizationError, EffectCancelledError) as exc:
            raise BeforeDispatchError("authorization ended before dispatch") from exc
        running = asyncio.ensure_future(operation())
        try:
            while not running.done():
                await asyncio.wait({running}, timeout=0.02)
                if not running.done():
                    verify()
            return running.result()
        finally:
            if not running.done():
                running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    async def run(
        self,
        *,
        task_id: str,
        key: str,
        request: object,
        operation: Callable[[], Awaitable[dict[str, Any]]],
        max_cny: float | Decimal,
        max_calls: int,
        reserve_cny: float | Decimal = 0,
        reserve_calls: int = 0,
        measure: Callable[[dict[str, Any]], float | Decimal] | None = None,
        cancel_event: threading.Event,
        max_attempts: int = 2,
        context: CallContext | None = None,
        now: datetime | None = None,
        usage: Callable[[dict[str, Any]], CallUsage] | None = None,
    ) -> dict[str, Any]:
        validate_budget_values(max_cny, max_calls)
        validate_budget_values(reserve_cny, reserve_calls)
        if reserve_cny > 0 and measure is None:
            raise ValueError("a paid reservation requires an explicit usage meter")
        if max_attempts not in (1, 2) or isinstance(max_attempts, bool):
            raise ValueError("effect attempts must be one or two")
        digest = hashlib.sha256(_json(request).encode()).hexdigest()
        if cancel_event.is_set():
            raise EffectCancelledError("operation cancelled before dispatch")
        with self._lock:
            # The write lock covers capacity check and intent, across connections.
            self.db.execute("BEGIN IMMEDIATE")
            try:
                authorized = self.db.execute(
                    "SELECT 1 FROM task_authorizations WHERE task_id=?", (task_id,)
                ).fetchone() is not None
                phase = None
                if authorized and context is None:
                    raise AuthorizationError("authorized tasks require an explicit call context")
                if context is not None:
                    if key != context.effect_key():
                        raise AuthorizationError("effect key must match the stable call identity")
                    phase = validate_call_context(
                        self.db, task_id, context, now=now or datetime.now(UTC),
                        reserve_cny=Decimal(str(reserve_cny)), reserve_calls=reserve_calls,
                    )
                budget = self.db.execute(
                    "SELECT * FROM effect_budgets WHERE task_id=?", (task_id,)
                ).fetchone()
                ceiling = str(Decimal(str(max_cny)))
                if budget is None:
                    self.db.execute(
                        "INSERT INTO effect_budgets VALUES (?, ?, ?)", (task_id, ceiling, max_calls)
                    )
                elif (
                    Decimal(budget["max_cny"]) != Decimal(ceiling)
                    or budget["max_calls"] != max_calls
                ):
                    raise EffectConflictError("task effect budget changed")
                row = self.db.execute(
                    "SELECT * FROM effects WHERE task_id=? AND effect_key=?", (task_id, key)
                ).fetchone()
                if row is not None:
                    if context is not None:
                        saved = self.db.execute(
                            "SELECT * FROM effect_contexts WHERE task_id=? AND effect_key=?",
                            (task_id, key),
                        ).fetchone()
                        if (saved is None or saved["call_kind"] != context.call_kind
                                or saved["authorization_id"] != context.authorization_id
                                or saved["operation_id"] != context.operation_id
                                or saved["attempt"] != context.attempt):
                            raise EffectConflictError("stored call context differs")
                    if (
                        row["request_hash"] != digest
                        or Decimal(row["reserve_cny"]) != Decimal(str(reserve_cny))
                        or row["reserve_calls"] != reserve_calls
                    ):
                        raise EffectConflictError("effect request changed")
                    if row["state"] == "completed":
                        value: dict[str, Any] = json.loads(row["result_json"])
                        self.db.commit()
                        return value
                    if row["state"] != "retryable":
                        raise EffectUncertainError("effect result is unknown or still in flight")
                    if row["attempts"] >= max_attempts:
                        raise EffectUncertainError("bounded pre-dispatch retry limit reached")
                if context is not None:
                    assert phase is not None
                    if row is None:
                        check_operation_attempt(self.db, task_id, context)
                    check_context_capacity(
                        self.db, task_id, context, phase,
                        reserve_cny=Decimal(str(reserve_cny)), reserve_calls=reserve_calls,
                    )
                if context is not None:
                    self.require_projection_synced(task_id)
                # Keep historical replay/unknown diagnostics, but gate every new
                # intent, including legacy callers without an authorization context.
                self._require_unfenced_restore()
                # Admission uses Decimal directly; summary floats are presentation only.
                entries = self.db.execute(
                    "SELECT * FROM effects WHERE task_id=? AND state!='retryable'", (task_id,),
                ).fetchall()
                committed = sum((
                    Decimal(r["measured_cny"]) if r["state"] == "completed" else
                    max(Decimal(r["reserve_cny"]), Decimal(r["measured_cny"] or "0"))
                    for r in entries
                ), Decimal(0))
                if (
                    committed + Decimal(str(reserve_cny)) > Decimal(ceiling)
                    or sum(r["reserve_calls"] for r in entries) + reserve_calls > max_calls
                ):
                    raise BudgetError("call reservation exceeds the approved task envelope")
                if row is None:
                    self.db.execute(
                        "INSERT INTO effects VALUES (?, ?, ?, ?, ?, 'pending', 1, NULL, NULL)",
                        (task_id, key, digest, str(reserve_cny), reserve_calls),
                    )
                    if context is not None:
                        self.db.execute(
                            "INSERT INTO effect_contexts VALUES (?,?,?,?,?,?,?,?,NULL,?)",
                            (task_id, key, context.authorization_id, context.operation_id,
                             context.call_kind, context.attempt,
                             int(context.call_kind == "local_model"),
                             int(context.call_kind == "external_request"),
                             (now or datetime.now(UTC)).astimezone(UTC).isoformat()),
                        )
                else:
                    self.db.execute(
                        "UPDATE effects SET state='pending', attempts=attempts+1 "
                        "WHERE task_id=? AND effect_key=?",
                        (task_id, key),
                    )
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
        try:
            if cancel_event.is_set():
                raise BeforeDispatchError("cancelled before dispatch")
            result = (await operation() if context is None else
                      await self._dispatch_authorized(
                          task_id=task_id, context=context, operation=operation,
                          cancel_event=cancel_event, reserve_cny=Decimal(str(reserve_cny)),
                          reserve_calls=reserve_calls, now=now,
                      ))
            cost = 0.0 if measure is None else measure(result)
            validate_budget_values(cost, 0)
            serialized = _json(result)
            usage_record = None if usage is None else usage(result).model_dump_json()
            if Decimal(str(cost)) > Decimal(str(reserve_cny)):
                raise MeasuredUsageError(
                    cost,
                    None if usage_record is None else CallUsage.model_validate_json(usage_record),
                )
            with self._lock, self.db:
                if context is not None and usage_record is not None:
                    self.db.execute(
                        "UPDATE effect_contexts SET usage_json=? WHERE task_id=? AND effect_key=?",
                        (usage_record, task_id, key),
                    )
                self.db.execute(
                    "UPDATE effects SET state='completed', measured_cny=?, result_json=? "
                    "WHERE task_id=? AND effect_key=?",
                    (str(cost), serialized, task_id, key),
                )
            if context is not None and self._projection_ledger is not None:
                # Failure stays visible and blocks the next new call, while the
                # confirmed result remains recoverable without external replay.
                self.project_usage(task_id, self._projection_ledger)
            return result
        except BeforeDispatchError:
            with self._lock, self.db:
                self.db.execute(
                    "UPDATE effects SET state='retryable' WHERE task_id=? AND effect_key=?",
                    (task_id, key),
                )
            raise
        except BaseException as exc:
            with self._lock, self.db:
                if isinstance(exc, MeasuredUsageError):
                    self.db.execute(
                        "UPDATE effects SET measured_cny=? WHERE task_id=? AND effect_key=?",
                        (str(exc.cost), task_id, key),
                    )
                    if context is not None and exc.usage is not None:
                        self.db.execute(
                            "UPDATE effect_contexts SET usage_json=? "
                            "WHERE task_id=? AND effect_key=?",
                            (exc.usage.model_dump_json(), task_id, key),
                        )
                self.db.execute(
                    "UPDATE effects SET state='unknown' WHERE task_id=? AND effect_key=?",
                    (task_id, key),
                )
            if isinstance(exc, Exception) and not isinstance(exc, EffectCancelledError):
                raise EffectUncertainError(
                    "dispatched effect did not durably confirm its result; reconciliation required"
                ) from exc
            raise

    def project_usage(self, task_id: str, ledger: RuntimeLedger) -> dict[str, Any]:
        """Replay only confirmed measurements, without rerunning external effects.

        Failed projection is explicit. The returned journal totals remain the
        accounting authority; an empty or stale RuntimeLedger is not zero spend.
        """
        with self._lock:
            rows = self.db.execute(
                "SELECT e.*,c.call_kind,c.usage_json FROM effects e "
                "JOIN effect_contexts c USING(task_id,effect_key) "
                "WHERE e.task_id=? AND e.measured_cny IS NOT NULL "
                "AND e.state IN ('completed','unknown') ORDER BY e.effect_key", (task_id,),
            ).fetchall()
            projected = 0
            pending = 0
            for row in rows:
                if row["call_kind"] == "stage":
                    continue
                try:
                    key, delta = self._projection_record(row, task_id)
                    ledger.project_confirmed_usage(
                        effect_key=key, task_id=task_id, delta=delta,
                    )
                    projected += 1
                except Exception:
                    # Do not expose storage errors or use a stale aggregate as actual usage.
                    pending += 1
            authoritative = self.summary(task_id)
            state = ("pending" if pending else "reconciliation_required"
                     if authoritative["uncertain_effects"] else "synced")
            return {"state": state, "projected_effects": projected,
                    "pending_effects": pending, "authoritative": authoritative}

    def summary(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM effects WHERE task_id=?", (task_id,)).fetchall()
            contexts = self.db.execute(
                "SELECT c.*,e.state FROM effect_contexts c "
                "JOIN effects e USING(task_id,effect_key) "
                "WHERE c.task_id=? AND e.state!='retryable'", (task_id,),
            ).fetchall()
        measured = sum(
            (Decimal(r["measured_cny"]) for r in rows if r["state"] == "completed"), Decimal(0)
        )
        held = sum(
            (
                max(Decimal(r["reserve_cny"]), Decimal(r["measured_cny"] or "0"))
                for r in rows
                if r["state"] in {"pending", "unknown"}
            ),
            Decimal(0),
        )
        return {
            "measured_cny": float(measured),
            "known_cny": float(sum((Decimal(r["measured_cny"])
                                    for r in rows if r["measured_cny"] is not None), Decimal(0))),
            "held_cny": float(held),
            "committed_cny": float(measured + held),
            "committed_calls": sum(r["reserve_calls"] for r in rows if r["state"] != "retryable"),
            "committed_local_calls": sum(r["reserve_local_calls"] for r in contexts),
            "committed_external_requests": sum(r["reserve_external_requests"] for r in contexts),
            "uncertain_effects": sum(r["state"] in {"pending", "unknown"} for r in rows),
            "completed_effects": sum(r["state"] == "completed" for r in rows),
            "is_actual_bill": False,
        }
