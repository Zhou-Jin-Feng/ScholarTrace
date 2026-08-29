from __future__ import annotations

from pathlib import Path

import pytest

from scholartrace.contracts import BudgetLimits, BudgetUsage
from scholartrace.workflow.events import replay_sse
from scholartrace.workflow.storage import (
    ArtifactConflictError,
    ArtifactStore,
    RuntimeEffectConflictError,
    RuntimeLedger,
    WorkflowBudgetExceededError,
)


def test_artifact_store_is_idempotent_and_rejects_conflicts(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts.sqlite")
    try:
        first = store.put(
            artifact_id="artifact:m3:test",
            artifact_type="tool_run",
            payload={"value": 1},
        )
        repeated = store.put(
            artifact_id="artifact:m3:test",
            artifact_type="tool_run",
            payload={"value": 1},
        )

        assert first == repeated
        assert store.count() == 1
        with pytest.raises(ArtifactConflictError):
            store.put(
                artifact_id="artifact:m3:test",
                artifact_type="tool_run",
                payload={"value": 2},
            )
    finally:
        store.close()


def test_budget_effects_are_exactly_once_and_fail_before_overrun(tmp_path: Path) -> None:
    ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
    limits = BudgetLimits(max_queries=1, max_api_calls=1)
    try:
        delta = BudgetUsage(queries=1, api_calls=1)
        first = ledger.charge(
            effect_key="effect:query:1", task_id="task:m3", delta=delta, limits=limits
        )
        repeated = ledger.charge(
            effect_key="effect:query:1", task_id="task:m3", delta=delta, limits=limits
        )

        assert first.queries == repeated.queries == 1
        with pytest.raises(RuntimeEffectConflictError, match="budget effect"):
            ledger.charge(
                effect_key="effect:query:1",
                task_id="task:m3",
                delta=BudgetUsage(queries=1),
                limits=limits,
            )
        with pytest.raises(WorkflowBudgetExceededError, match="queries"):
            ledger.charge(
                effect_key="effect:query:2",
                task_id="task:m3",
                delta=delta,
                limits=limits,
            )
        assert ledger.usage("task:m3").queries == 1
    finally:
        ledger.close()


def test_persisted_events_replay_after_last_event_id(tmp_path: Path) -> None:
    ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
    try:
        first = ledger.append_event(
            stable_key="event:key:1",
            task_id="task:m3",
            node="search",
            kind="started",
        )
        repeated = ledger.append_event(
            stable_key="event:key:1",
            task_id="task:m3",
            node="search",
            kind="started",
        )
        second = ledger.append_event(
            stable_key="event:key:2",
            task_id="task:m3",
            node="search",
            kind="finished",
        )

        assert first.event_id == repeated.event_id
        with pytest.raises(RuntimeEffectConflictError, match="event content"):
            ledger.append_event(
                stable_key="event:key:1",
                task_id="task:m3",
                node="search",
                kind="different",
            )
        assert ledger.event_count("task:m3") == 2
        replay = ledger.replay(task_id="task:m3", last_event_id=first.event_id)
        assert [event.event_id for event in replay] == [second.event_id]
        encoded = replay_sse(ledger, task_id="task:m3", last_event_id=first.event_id)
        assert encoded[0].startswith(f"id: {second.event_id}\nevent: finished\n")
    finally:
        ledger.close()
