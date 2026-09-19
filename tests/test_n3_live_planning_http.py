"""Exercise the actual provider parser and journal with synthetic HTTP only."""

import json
import threading
from datetime import date
from pathlib import Path

import httpx
import pytest
from test_task_authorization import NOW, grant, phase, policy

from scholartrace.contracts import BudgetLimits
from scholartrace.delivery.authorization import AuthorizationError
from scholartrace.delivery.effects import EffectConflictError, EffectJournal, EffectUncertainError
from scholartrace.delivery.live_planning import MeteredPlanFactory
from scholartrace.workflow.storage import RuntimeLedger


@pytest.mark.parametrize("scenario", ["replay", "drift", "unknown", "unapproved", "source"])
def test_coordinator_http_is_authorized_metered_and_replayable(tmp_path, scenario):
    remote = policy().remote.model_copy(update={"max_input_tokens": 20000})
    approved = policy(remote=remote)
    ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
    journal = EffectJournal(tmp_path / "effects.sqlite", projection_ledger=ledger)
    calls, closed = [], []

    class Transport(httpx.MockTransport):
        async def aclose(self):
            closed.append(True)
            await super().aclose()

    def respond(request):
        calls.append(request.url.path)
        assert request.headers["idempotency-key"].startswith("authorized:")
        payload = json.loads(request.content)
        assert payload["model"] == "synthetic"
        if scenario == "unknown":
            raise httpx.ReadTimeout("synthetic response loss")
        draft = {
            "title": "Synthetic research plan", "objective": "Compare verified evidence",
            "inclusion_criteria": ["Full text available"],
            "exclusion_criteria": ["Opinion only"],
            "sources": ["crossref" if scenario == "source" else "arxiv"],
            "subquestions": [
                {"question": "Which conditions improve retrieval?",
                 "evidence_required": "fulltext", "priority": "high"},
                {"question": "Which conditions limit retrieval?",
                 "evidence_required": "fulltext", "priority": "normal"},
            ],
        }
        return httpx.Response(200, json={
            "model": "synthetic",
            "choices": [{"message": {"content": json.dumps(draft)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        })

    factory = MeteredPlanFactory(
        policy=approved, api_key="x",
        plan_limits=BudgetLimits(max_cost_cny=0.8, max_api_calls=3, max_fulltext_papers=3),
        retrieval_cutoff=date(2026, 9, 18), transport_factory=lambda: Transport(respond),
    )
    kwargs = dict(task_id="task:synthetic", question="Compare evidence retrieval methods",
                  journal=journal, ledger=ledger, cancel_event=threading.Event())
    try:
        journal.authorize_task(grant(
            policy=approved, deadline_at="2099-01-01T00:00:00+00:00",
        ), now=NOW)
        if scenario == "unapproved":
            with pytest.raises(AuthorizationError):
                factory(**kwargs)
            assert calls == []
            return
        approval = phase(policy_sha256=approved.digest())
        journal.prepare_phase(approval, now=NOW)
        journal.activate_phase(approval, now=NOW)
        if scenario in {"unknown", "source"}:
            error = EffectUncertainError if scenario == "unknown" else AuthorizationError
            for _ in range(2):
                with pytest.raises(error):
                    factory(**kwargs)
            assert len(calls) == 1
            if scenario == "unknown":
                assert journal.summary("task:synthetic")["uncertain_effects"] == 1
            else:
                assert ledger.usage("task:synthetic").api_calls == 1
            return
        first = factory(**kwargs)
        assert len(first.subquestions) == 2
        assert first.inclusion_criteria == ["Full text available"]
        if scenario == "drift":
            kwargs["question"] = "A different research question entirely"
            with pytest.raises(EffectConflictError):
                factory(**kwargs)
        else:
            assert factory(**kwargs) == first
        assert len(calls) == 1
        assert len(closed) == 2
        assert ledger.usage("task:synthetic").api_calls == 1
        assert ledger.usage("task:synthetic").external_cost_cny == pytest.approx(0.00012)
    finally:
        journal.close()
        ledger.close()


@pytest.mark.parametrize('restart', [False, True])
def test_service_persists_provider_plan_and_retries_metadata_without_new_http(
    tmp_path, monkeypatch, restart,
):
    from test_n2_authorization_service import authorization

    from scholartrace.delivery.live import LiveComposition
    from scholartrace.delivery.models import TaskCreateRequest
    from scholartrace.delivery.service import M6TaskService

    approved = policy(remote=policy().remote.model_copy(update={"max_input_tokens": 20000}))
    calls = []

    def respond(request):
        calls.append(True)
        return httpx.Response(200, json={
            "model": "synthetic", "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            "choices": [{"message": {"content": json.dumps({
                "title": "Retrieval evidence", "objective": "Compare fulltext findings",
                "sources": ["arxiv"], "inclusion_criteria": ["Empirical evaluation"],
                "exclusion_criteria": ["Opinion pieces"],
                "subquestions": [{"question": "Which retrieval approaches work?",
                                  "evidence_required": "fulltext", "priority": "critical"}],
            })}}],
        })

    def no_execution(**kwargs):
        raise AssertionError("planning must not start research")

    runner = LiveComposition(
        policy=approved, year_from=2020, evidence_kind="synthetic",
            plan_factory=MeteredPlanFactory(
            policy=approved, api_key="x",
            plan_limits=BudgetLimits(max_cost_cny=0.8, max_api_calls=3,
                                     max_fulltext_papers=3, max_duration_seconds=500),
            retrieval_cutoff=date(2026, 9, 18),
            transport_factory=lambda: httpx.MockTransport(respond),
        ), pipeline_factory=no_execution,
    )
    service = M6TaskService(root=Path(__file__).resolve().parents[1],
                           data_dir=tmp_path, live_runner=runner)
    try:
        task_id = service.create_task(TaskCreateRequest(
            question="Compare empirical retrieval approaches", execution_mode="real",
        ))["task_id"]
        service.acknowledge_plan_cost(
            task_id, acknowledged_max_cny=0.2,
            authorization=authorization().model_copy(update={"policy_sha256": approved.digest()}),
        )

        def fail_save(**kwargs):
            raise OSError("synthetic metadata failure")

        with monkeypatch.context() as patch:
            patch.setattr(service.plans, "save_plan", fail_save)
            with pytest.raises(OSError):
                service.generate_plan(task_id)
        if restart:
            service.close()
            service = M6TaskService(root=Path(__file__).resolve().parents[1],
                                    data_dir=tmp_path, live_runner=runner)
        result = service.generate_plan(task_id)
        assert service.generate_plan(task_id) == result
        assert result["generated_by"] == "api-strong"
        assert result["details"]["subquestions"][0]["priority"] == "critical"
        assert service.summary(task_id)["status"] == "waiting_approval"
        assert service.ledger.usage(task_id).api_calls == 1
        assert calls == [True]
    finally:
        service.close()
