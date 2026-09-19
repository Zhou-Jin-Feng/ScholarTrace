from __future__ import annotations

import asyncio
import threading

import pytest


def test_completed_effect_replays_after_restart_without_repeating_side_effect(tmp_path):
    from scholartrace.delivery.effects import EffectJournal

    path = tmp_path / "effects.sqlite"
    calls = []

    async def operation():
        calls.append("dispatch")
        return {"result": "verified", "cost": 0.2}

    async def run():
        for _ in range(2):
            journal = EffectJournal(path)
            try:
                value = await journal.run(
                    task_id="task:one",
                    key="call:verify",
                    request={"v": 1},
                    operation=operation,
                    max_cny=0.5,
                    max_calls=1,
                    reserve_cny=0.4,
                    reserve_calls=1,
                    measure=lambda r: r["cost"],
                    cancel_event=threading.Event(),
                )
                assert value["result"] == "verified"
                assert journal.summary("task:one")["measured_cny"] == 0.2
            finally:
                journal.close()
        assert calls == ["dispatch"]

    asyncio.run(run())


def test_concurrent_connections_cannot_oversubscribe_and_unknown_never_replays(tmp_path):
    from scholartrace.delivery.budget import BudgetError
    from scholartrace.delivery.effects import EffectJournal, EffectUncertainError

    path = tmp_path / "effects.sqlite"

    async def run():
        first, second = EffectJournal(path), EffectJournal(path)
        started, finish = asyncio.Event(), asyncio.Event()

        async def uncertain():
            started.set()
            await finish.wait()
            raise TimeoutError("response lost after dispatch")

        async def forbidden():
            pytest.fail("must not dispatch")

        args = dict(
            task_id="task:one",
            request={},
            max_cny=0.5,
            max_calls=2,
            reserve_cny=0.4,
            reserve_calls=1,
            measure=lambda r: 0.2,
            cancel_event=threading.Event(),
        )
        task = asyncio.create_task(first.run(key="one", operation=uncertain, **args))
        await started.wait()
        try:
            with pytest.raises(BudgetError):
                await second.run(key="two", operation=forbidden, **args)
            finish.set()
            with pytest.raises(EffectUncertainError):
                await task
            with pytest.raises(EffectUncertainError):
                await second.run(key="one", operation=forbidden, **args)
            assert second.summary("task:one")["held_cny"] == 0.4
            assert second.summary("task:one")["uncertain_effects"] == 1
        finally:
            finish.set()
            first.close()
            second.close()

    asyncio.run(run())


def test_only_explicit_pre_dispatch_failures_are_retryable_and_bounded(tmp_path):
    from scholartrace.delivery.effects import (
        BeforeDispatchError,
        EffectJournal,
        EffectUncertainError,
    )

    async def run():
        journal = EffectJournal(tmp_path / "effects.sqlite")
        calls = []

        async def not_sent():
            calls.append(1)
            raise BeforeDispatchError("adapter rejected before network dispatch")

        args = dict(
            task_id="task:one",
            key="one",
            request={},
            operation=not_sent,
            max_cny=1,
            max_calls=1,
            reserve_cny=1,
            reserve_calls=1,
            measure=lambda r: 0,
            cancel_event=threading.Event(),
        )
        try:
            for _ in range(2):
                with pytest.raises(BeforeDispatchError):
                    await journal.run(**args)
            with pytest.raises(EffectUncertainError):
                await journal.run(**args)
            assert len(calls) == 2
            assert journal.summary("task:one")["committed_cny"] == 0
        finally:
            journal.close()

    asyncio.run(run())


def test_cancel_before_dispatch_and_changed_request_do_not_call_backend(tmp_path):
    from scholartrace.delivery.effects import (
        EffectCancelledError,
        EffectConflictError,
        EffectJournal,
    )

    async def run():
        journal = EffectJournal(tmp_path / "effects.sqlite")
        cancel = threading.Event()
        calls = []

        async def operation():
            calls.append(1)
            return {"value": "ok"}

        args = dict(
            task_id="task:one",
            key="one",
            request={},
            operation=operation,
            max_cny=0,
            max_calls=0,
            cancel_event=cancel,
        )
        try:
            cancel.set()
            with pytest.raises(EffectCancelledError):
                await journal.run(**args)
            cancel.clear()
            await journal.run(**args)
            with pytest.raises(EffectConflictError):
                await journal.run(**{**args, "request": {"changed": True}})
            assert calls == [1]
        finally:
            journal.close()

    asyncio.run(run())


def test_paid_reservation_requires_explicit_meter(tmp_path):
    from scholartrace.delivery.effects import EffectJournal

    async def run():
        journal = EffectJournal(tmp_path / "effects.sqlite")

        async def forbidden():
            pytest.fail("missing meter must not dispatch")

        try:
            with pytest.raises(ValueError, match="meter"):
                await journal.run(
                    task_id="task:one",
                    key="one",
                    request={},
                    operation=forbidden,
                    max_cny=1,
                    max_calls=1,
                    reserve_cny=0.5,
                    reserve_calls=1,
                    cancel_event=threading.Event(),
                )
        finally:
            journal.close()

    asyncio.run(run())


def test_provider_overrun_retains_known_exposure_instead_of_only_smaller_reservation(tmp_path):
    from scholartrace.delivery.effects import EffectJournal, EffectUncertainError

    async def run():
        journal = EffectJournal(tmp_path / "effects.sqlite")

        async def operation():
            return {"cost": 1.5}

        try:
            with pytest.raises(EffectUncertainError):
                await journal.run(
                    task_id="task:one",
                    key="one",
                    request={},
                    operation=operation,
                    max_cny=1,
                    max_calls=1,
                    reserve_cny=1,
                    reserve_calls=1,
                    measure=lambda r: r["cost"],
                    cancel_event=threading.Event(),
                )
            assert journal.summary("task:one")["committed_cny"] == 1.5
            assert journal.summary("task:one")["uncertain_effects"] == 1
        finally:
            journal.close()

    asyncio.run(run())
