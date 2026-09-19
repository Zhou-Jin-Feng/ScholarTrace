"""Synthetic N3 dispatch boundary regressions; never use real HTTP or task data."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from scholartrace.delivery import effects
from scholartrace.delivery.authorization import CallContext, PhaseGrant, RuntimePolicy, TaskGrant
from scholartrace.delivery.effects import (
    BeforeDispatchError,
    EffectCancelledError,
    EffectJournal,
    EffectUncertainError,
)


@pytest.fixture
def setup_journal(tmp_path):
    journals = []

    def build(*, seconds=60):
        journal = EffectJournal(tmp_path / f"effects-{len(journals)}.sqlite")
        journals.append(journal)
        policy = RuntimePolicy(
            remote={
                "endpoint": "https://synthetic.invalid/v1/chat/completions",
                "model": "synthetic",
                "model_version": "v1",
                "protocol": "chat_completions",
                "input_cny_per_million": "1",
                "output_cny_per_million": "2",
                "price_observed_at": datetime.now(UTC).isoformat(),
                "max_input_tokens": 1000,
                "max_output_tokens": 20,
            },
            data_fields=("question",),
            allowed_search_providers=("arxiv",),
            max_papers=3,
        )
        journal.authorize_task(
            TaskGrant(
                task_id="task",
                policy=policy,
                max_cny="1",
                max_remote_calls=4,
                max_local_calls=2,
                max_external_requests=2,
                deadline_at=(datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(),
            )
        )
        grant = PhaseGrant(
            task_id="task",
            authorization_id="execution:1",
            phase="execution",
            generation=1,
            policy_sha256=policy.digest(),
            max_cny="1",
            max_remote_calls=4,
            max_local_calls=2,
            max_external_requests=2,
            acknowledgement_sha256="a" * 64,
            plan_version=1,
            plan_digest="b" * 64,
        )
        journal.prepare_phase(grant)
        journal.activate_phase(grant)
        context = CallContext(
            authorization_id=grant.authorization_id,
            operation_id="operation",
            call_kind="remote_model",
            policy_sha256=policy.digest(),
            plan_version=1,
            plan_digest="b" * 64,
        )
        return journal, context

    yield build
    for journal in journals:
        journal.close()


def invoke(journal, context, operation, event=None, **kwargs):
    return journal.run(
        task_id="task",
        key=context.effect_key(),
        request={"synthetic": True},
        operation=operation,
        max_cny=Decimal("1"),
        max_calls=4,
        reserve_cny=Decimal("0.25"),
        reserve_calls=1,
        measure=lambda value: Decimal(value["cost"]),
        cancel_event=event if event is not None else threading.Event(),
        context=context,
        **kwargs,
    )


def state(journal, context):
    return journal.db.execute(
        "SELECT * FROM effects WHERE task_id=? AND effect_key=?",
        ("task", context.effect_key()),
    ).fetchone()


@pytest.mark.parametrize("stop", ["deadline", "cancel", "task_revoked", "phase_revoked"])
def test_stop_between_intent_and_operation_never_dispatches(setup_journal, monkeypatch, stop):
    journal, context = setup_journal()
    event = threading.Event()
    original = journal.db
    calls = []

    class Clock(datetime):
        offset = timedelta()

        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + cls.offset

    monkeypatch.setattr(effects, "datetime", Clock)

    class CommitBoundary:
        def __getattr__(self, name):
            return getattr(original, name)

        def __enter__(self):
            original.__enter__()
            return self

        def __exit__(self, *args):
            return original.__exit__(*args)

        def commit(self):
            original.commit()
            journal.db = original
            if stop == "deadline":
                Clock.offset = timedelta(seconds=120)
            elif stop == "cancel":
                event.set()
            elif stop == "task_revoked":
                journal.revoke_task("task")
            else:
                journal.revoke_phase("task", "execution")

    journal.db = CommitBoundary()

    async def operation():
        calls.append("sent")
        return {"cost": "0.1"}

    async def run():
        with pytest.raises(BeforeDispatchError):
            await invoke(journal, context, operation, event)
        assert calls == []
        assert state(journal, context)["state"] == "retryable"
        assert journal.summary("task")["held_cny"] == 0

    asyncio.run(run())


@pytest.mark.parametrize("stop", ["deadline", "cancel", "task_revoked", "phase_revoked", "async"])
def test_stop_during_operation_cancels_and_retains_unknown_hold(setup_journal, stop):
    journal, context = setup_journal(seconds=0.12 if stop == "deadline" else 60)
    event = threading.Event()
    calls = []

    async def run():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def operation():
            calls.append("sent")
            started.set()
            try:
                await asyncio.sleep(0.4)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return {"cost": "0.1"}

        task = asyncio.create_task(invoke(journal, context, operation, event))
        await started.wait()
        if stop == "cancel":
            event.set()
        elif stop == "task_revoked":
            journal.revoke_task("task")
        elif stop == "phase_revoked":
            journal.revoke_phase("task", "execution")
        elif stop == "async":
            task.cancel()
        expected = (
            asyncio.CancelledError
            if stop == "async"
            else (
                EffectCancelledError,
                EffectUncertainError,
            )
        )
        with pytest.raises(expected):
            await task
        await asyncio.sleep(0)
        assert cancelled.is_set()
        row = state(journal, context)
        assert row["state"] == "unknown"
        assert Decimal(row["reserve_cny"]) == Decimal("0.25")
        assert row["measured_cny"] is None
        assert journal.summary("task")["held_cny"] == 0.25
        assert calls == ["sent"]

    asyncio.run(run())


def test_failed_projection_blocks_next_operation_and_restart_until_reconciled(
    tmp_path, monkeypatch
):
    from test_task_authorization import NOW, context, grant, phase
    from test_task_authorization import invoke as old_invoke

    from scholartrace.delivery.authorization import AuthorizationError, CallUsage
    from scholartrace.workflow.storage import RuntimeLedger

    ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
    journal = EffectJournal(tmp_path / "effects.sqlite", projection_ledger=ledger)
    journal.authorize_task(grant(), now=NOW)
    journal.prepare_phase(phase(), now=NOW)
    journal.activate_phase(phase(), now=NOW)
    calls = []

    async def operation():
        calls.append(True)
        return {"cost": 0.1}

    async def invoke_current(ctx):
        return await old_invoke(
            journal, ctx, operation, usage=lambda r: CallUsage(input_tokens=10, output_tokens=2)
        )

    def fail(**kwargs):
        raise OSError("synthetic projection unavailable")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(ledger, "project_confirmed_usage", fail)
            assert asyncio.run(invoke_current(context())) == {"cost": 0.1}
            assert journal.project_usage("task:synthetic", ledger)["state"] == "pending"
            with pytest.raises(AuthorizationError, match="projection"):
                asyncio.run(invoke_current(context("next")))
        journal.close()
        journal = EffectJournal(tmp_path / "effects.sqlite", projection_ledger=ledger)
        with pytest.raises(AuthorizationError, match="projection"):
            asyncio.run(invoke_current(context("next")))
        # Confirmed replay is safe, but does not dispatch or silently unblock new work.
        assert asyncio.run(invoke_current(context())) == {"cost": 0.1}
        assert calls == [True]
        assert journal.project_usage("task:synthetic", ledger)["state"] == "synced"
        assert asyncio.run(invoke_current(context("next"))) == {"cost": 0.1}
        assert calls == [True, True]
        assert ledger.usage("task:synthetic").external_cost_cny == 0.2
    finally:
        journal.close()
        ledger.close()


@pytest.mark.parametrize("unknown_child", [False, True])
def test_explicit_resume_only_retries_zero_cost_stage_with_confirmed_children(
    setup_journal,
    tmp_path,
    unknown_child,
):
    from scholartrace.delivery.authorization import AuthorizationError, CallUsage
    from scholartrace.workflow.storage import RuntimeLedger

    journal, remote_context = setup_journal()
    ledger = RuntimeLedger(tmp_path / "projection.sqlite")
    journal._projection_ledger = ledger
    stage_context = remote_context.model_copy(
        update={"call_kind": "stage", "operation_id": "stage"}
    )
    calls = []

    async def child():
        calls.append(True)
        if unknown_child:
            raise TimeoutError("synthetic unknown external effect")
        return {"cost": "0.1"}

    async def orchestration(fail):
        await invoke(
            journal,
            remote_context,
            child,
            usage=lambda r: CallUsage(input_tokens=10, output_tokens=2),
        )
        if fail:
            raise OSError("synthetic stage result publication failure")
        return {"done": True}

    async def stage(fail):
        return await journal.run(
            task_id="task",
            key=stage_context.effect_key(),
            request={"synthetic": "stage"},
            operation=lambda: orchestration(fail),
            max_cny=1,
            max_calls=4,
            cancel_event=threading.Event(),
            context=stage_context,
        )

    try:
        with pytest.raises(EffectUncertainError):
            asyncio.run(stage(True))
        assert state(journal, stage_context)["state"] == "unknown"
        if unknown_child:
            with pytest.raises(AuthorizationError, match="unknown external"):
                journal.reconcile_stage_resume("task", plan_version=1, plan_digest="b" * 64)
            assert state(journal, remote_context)["state"] == "unknown"
            assert state(journal, stage_context)["state"] == "unknown"
        else:
            journal.reconcile_stage_resume("task", plan_version=1, plan_digest="b" * 64)
            assert asyncio.run(stage(False)) == {"done": True}
            assert state(journal, stage_context)["attempts"] == 2
            assert journal.summary("task")["known_cny"] == 0.1
        assert calls == [True]
    finally:
        ledger.close()
