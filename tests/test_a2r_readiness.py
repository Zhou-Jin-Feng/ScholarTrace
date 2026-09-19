"""Configuration is not a successful dependency probe."""

from scholartrace.delivery.executors import RealTaskExecutor
from scholartrace.delivery.models import TaskCreateRequest
from scholartrace.delivery.service import M6TaskService


def test_configured_unprobed_dependencies_are_unknown_and_queue_can_drain(tmp_path):
    executor = RealTaskExecutor(
        documind_available=True,
        local_model_available=True,
        api_strong_enabled=True,
        search_providers_configured=True,
        pipeline_stages_wired=True,
    )
    service = M6TaskService(root=tmp_path, data_dir=tmp_path, real_executor=executor)
    try:
        report = service.dependency_report()
        for item in report["dependencies"].values():
            assert item["state"] == "unknown"
            assert item["configured"] is True
        assert report["overall"] != "ready"
        task_id = service.create_task(TaskCreateRequest(question="Unknown planning cost"))[
            "task_id"
        ]
        estimate = service.estimate_plan(task_id)
        assert estimate["estimated_cny"] is None
        assert estimate["estimate_source"] == "unavailable"
        service._executor.shutdown()
        drained = service.dependency_report()
        assert drained["api"] == "draining"
        assert drained["demo_mode"]["state"] == "unavailable"
    finally:
        service.close()


def test_paid_switch_without_key_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOLARTRACE_PAID_ROUTES_ENABLED", "true")
    monkeypatch.delenv("SCHOLARTRACE_API_STRONG_KEY", raising=False)
    service = M6TaskService(root=tmp_path, data_dir=tmp_path)
    try:
        assert service.dependency_report()["dependencies"]["api_strong"]["state"] == "disabled"
    finally:
        service.close()
