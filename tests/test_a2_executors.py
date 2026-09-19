"""Executor seam: demo and real never share a path, and real fails closed."""

from __future__ import annotations

import threading

import pytest

from scholartrace.delivery.executors import (
    DemoTaskExecutor,
    DependencyKind,
    ExecutionMode,
    RealExecutionUnavailableError,
    RealTaskExecutor,
    select_executor,
)

ALL_PRESENT = {
    "documind_available": True,
    "local_model_available": True,
    "api_strong_enabled": True,
    "search_providers_configured": True,
}


def test_demo_executor_runs_the_existing_deterministic_path() -> None:
    calls: list[str] = []

    def run_demo(task_id: str, *, cancel_event: threading.Event) -> dict[str, object]:
        calls.append(task_id)
        return {"outcome": "completed", "papers": 4}

    executor = DemoTaskExecutor(run_demo)
    result = executor.run("task:1", cancel_event=threading.Event())
    assert executor.mode is ExecutionMode.DEMO
    assert executor.preflight() == []
    assert calls == ["task:1"]
    assert result["outcome"] == "completed"


def test_real_executor_reports_every_missing_dependency_separately() -> None:
    executor = RealTaskExecutor(
        documind_available=False,
        local_model_available=False,
        api_strong_enabled=False,
        search_providers_configured=False,
    )
    kinds = {gap.kind for gap in executor.preflight()}
    assert kinds == {
        DependencyKind.SEARCH_PROVIDER,
        DependencyKind.DOCUMIND,
        DependencyKind.LOCAL_MODEL,
        DependencyKind.API_STRONG,
    }


def test_real_executor_refuses_instead_of_falling_back_to_demo() -> None:
    executor = RealTaskExecutor(**{**ALL_PRESENT, "api_strong_enabled": False})
    with pytest.raises(RealExecutionUnavailableError) as excinfo:
        executor.run("task:1", cancel_event=threading.Event())
    payload = excinfo.value.as_public_payload()
    assert payload["code"] == "dependency_unavailable"
    assert [item["dependency"] for item in payload["unmet_dependencies"]] == ["api_strong"]


def test_refusal_reasons_carry_no_credentials_or_content() -> None:
    executor = RealTaskExecutor(
        documind_available=False,
        local_model_available=False,
        api_strong_enabled=False,
        search_providers_configured=False,
    )
    forbidden = ("key", "token", "secret", "bearer", "password", "prompt", "http://", "https://")
    for gap in executor.preflight():
        blob = f"{gap.reason} {gap.remediation or ''}".lower()
        for needle in forbidden:
            assert needle not in blob, f"{gap.kind.value} leaked {needle!r}"


def test_unwired_stages_refuse_rather_than_produce_a_fake_result() -> None:
    executor = RealTaskExecutor(**ALL_PRESENT, pipeline_stages_wired=False)
    # Dependencies satisfied, but the pipeline is not assembled: the honest
    # outcome is a refusal, not an empty-but-successful-looking report.
    with pytest.raises(RealExecutionUnavailableError):
        executor.run("task:1", cancel_event=threading.Event())


def test_selection_has_no_implicit_fallback_between_modes() -> None:
    demo = DemoTaskExecutor(lambda task_id, *, cancel_event: {"outcome": "completed"})
    real = RealTaskExecutor(**ALL_PRESENT)
    assert select_executor(ExecutionMode.DEMO, demo=demo, real=real) is demo
    assert select_executor(ExecutionMode.REAL, demo=demo, real=real) is real


def test_api_strong_gap_is_a_policy_gate_without_remediation() -> None:
    executor = RealTaskExecutor(**{**ALL_PRESENT, "api_strong_enabled": False})
    gap = next(g for g in executor.preflight() if g.kind is DependencyKind.API_STRONG)
    # Nothing the operator can flip locally: it needs an approval decision.
    assert gap.remediation is None
    assert "approved" in gap.reason
