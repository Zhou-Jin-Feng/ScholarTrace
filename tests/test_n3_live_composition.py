from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest


def _policy():
    from datetime import UTC, datetime

    from scholartrace.delivery.authorization import RuntimePolicy

    now = datetime.now(UTC).isoformat()
    return RuntimePolicy(
        remote={
            "endpoint": "https://provider.invalid/v1/chat/completions",
            "model": "approved",
            "model_version": "v1",
            "protocol": "chat_completions",
            "input_cny_per_million": "1",
            "output_cny_per_million": "1",
            "price_observed_at": now,
            "max_input_tokens": 1000,
            "max_output_tokens": 1000,
        },
        data_fields=("question",),
        allowed_search_providers=("arxiv",),
        max_papers=3,
    )


def test_live_root_rejects_fixture_pipeline_before_execution(tmp_path):
    from scholartrace.delivery.live import LiveComposition

    root = LiveComposition(
        policy=_policy(),
        year_from=2020,
        plan_factory=lambda **kwargs: object(),
        pipeline_factory=lambda **kwargs: SimpleNamespace(
            fixture_mode=True, execution_kind="offline_fixture"
        ),
    )
    with pytest.raises(RuntimeError, match="fixture"):
        root.run(
            question="approved question",
            plan=object(),  # type: ignore[arg-type]
            journal=object(),  # type: ignore[arg-type]
            ledger=object(),  # type: ignore[arg-type]
            cancel_event=threading.Event(),
            on_event=lambda event: None,
        )


def test_live_root_rejects_non_plan_factory_output():
    from scholartrace.delivery.live import LiveComposition

    root = LiveComposition(
        policy=_policy(),
        year_from=2020,
        plan_factory=lambda **kwargs: object(),
        pipeline_factory=lambda **kwargs: object(),
    )
    with pytest.raises(TypeError, match="ResearchPlan"):
        root.generate_plan(
            task_id="task:live",
            question="approved question",
            journal=object(),  # type: ignore[arg-type]
            ledger=object(),  # type: ignore[arg-type]
            cancel_event=threading.Event(),
        )
