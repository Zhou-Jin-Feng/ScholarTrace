"""Entire production pipeline over synthetic HTTP, with no socket or paid calls."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import date

import httpx
import pytest
from a2_core_fixtures import approved_plan
from production_http_peer import production_http_peer
from production_synthetic import research_responder
from test_task_authorization import NOW, grant, phase, policy

from scholartrace.contracts import BudgetLimits, ResearchSubquestion
from scholartrace.delivery.authorization import AuthorizationError
from scholartrace.delivery.effects import EffectJournal, EffectUncertainError
from scholartrace.delivery.plans import BudgetPlan, PlanDetails, compute_plan_digest
from scholartrace.delivery.production import ProductionResearchRunner
from scholartrace.workflow.storage import RuntimeLedger


@pytest.mark.parametrize("question_count", [1, 2])
@pytest.mark.parametrize("wire", ["mock", "http"])
@pytest.mark.parametrize(
    "lost_response",
    [None, "binding_write", "retrieval_barrier",
     "/api/v1/documents", "/api/chat", "/v1/chat/completions"],
)
def test_production_assembly_executes_all_adapters_and_replays(
    tmp_path, lost_response, monkeypatch, wire, question_count
):
    requests = []

    original_response = research_responder(requests, lost_response)
    retrieval_entered = threading.Event()
    retrieval_release = threading.Event()

    def respond(request):
        response = original_response(request)
        if (lost_response == 'retrieval_barrier'
            and request.url.path == '/api/v1/retrieve'
            and requests.count(('POST', '/api/v1/retrieve')) == 3 * question_count):
            retrieval_entered.set()
            assert retrieval_release.wait(timeout=10), 'retrieval barrier was not released'
        return response

    remote = policy().remote.model_copy(
        update={"max_input_tokens": 50000, "max_output_tokens": 4096}
    )
    local = remote.model_copy(
        update={
            "endpoint": "http://127.0.0.1:11434/api/chat",
            "protocol": "ollama",
            "model": "local-synthetic",
            "input_cny_per_million": "0",
            "output_cny_per_million": "0",
        }
    )
    approved = policy(
        remote=remote,
        local=local,
        documind_url="http://documind.test",
        data_fields=(
            "question",
            "selected_pdf",
            "paper_metadata",
            "retrieved_chunks",
            "claims",
            "evidence_quotes",
            "verification_results",
        ),
    )
    plan = approved_plan("task:synthetic")
    details = PlanDetails(
        title="Retrieval evidence",
        objective="Compare retrieval",
        inclusion_criteria=("Full text",),
        retrieval_cutoff=date(2026, 9, 18),
        subquestions=(
            ResearchSubquestion(
                subquestion_id="q:1",
                question=plan.sub_questions[0],
                evidence_required="fulltext",
                priority="high",
            ),
        ),
    )
    if question_count == 2:
        details = details.model_copy(
            update={
                "subquestions": (
                    *details.subquestions,
                    ResearchSubquestion(
                        subquestion_id="q:2",
                        question="What limits retrieval?",
                        evidence_required="fulltext",
                        priority="normal",
                    ),
                )
            }
        )
        plan = plan.model_copy(update={"sub_questions": [q.question for q in details.subquestions]})
    budget = BudgetPlan(
        max_cny=1,
        max_api_calls=10,
        max_wall_clock_seconds=120,
        estimate_source="approved_runtime_policy",
    )
    digest = compute_plan_digest(
        sub_questions=tuple(plan.sub_questions),
        source_scope=plan.source_scope,
        exclusions=(),
        budget_plan=budget,
        details=details,
    )
    plan = plan.model_copy(
        update={
            "generated_by": "api-strong",
            "details": details,
            "budget_plan": budget,
            "plan_digest": digest,
        }
    )
    ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
    journal = EffectJournal(tmp_path / "effects.sqlite", projection_ledger=ledger)
    journal.authorize_task(
        grant(
            policy=approved,
            max_remote_calls=10,
            max_local_calls=10,
            max_external_requests=30,
            deadline_at="2099-01-01T00:00:00+00:00",
        ),
        now=NOW,
    )
    execution = phase(
        authorization_id="execution:1",
        phase="execution",
        policy_sha256=approved.digest(),
        plan_version=1,
        plan_digest=digest,
        max_cny="1",
        max_remote_calls=10,
        max_local_calls=10,
        max_external_requests=30,
    )
    journal.prepare_phase(execution, now=NOW)
    journal.activate_phase(execution, now=NOW)
    resources = ExitStack()
    transport_factory = (
        resources.enter_context(production_http_peer(respond))
        if wire == "http"
        else lambda: httpx.MockTransport(respond)
    )
    runner = ProductionResearchRunner(
        policy=approved,
        api_key="x",
        data_dir=tmp_path / "research",
        year_from=2020,
        retrieval_cutoff=date(2026, 9, 18),
        plan_limits=BudgetLimits(max_fulltext_papers=3, max_api_calls=10, max_cost_cny=1),
        transport_factory=transport_factory,
    )
    try:
        if lost_response == 'retrieval_barrier':
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    runner.run, question='What determines retrieval?', plan=plan,
                    journal=journal, ledger=ledger, cancel_event=threading.Event(),
                    on_event=lambda e: None,
                )
                try:
                    assert retrieval_entered.wait(timeout=10)
                    assert not future.done()
                    assert ('POST', '/api/chat') not in requests
                    assert ('POST', '/v1/chat/completions') not in requests
                finally:
                    retrieval_release.set()
                future.result(timeout=20)
        elif lost_response == "binding_write":
            from scholartrace.evidence.bindings import DocuMindBindingRepository

            def fail_binding(*args, **kwargs):
                raise OSError("synthetic local binding write failure")

            with monkeypatch.context() as patch:
                patch.setattr(DocuMindBindingRepository, "put", fail_binding)
                with pytest.raises(EffectUncertainError):
                    runner.run(
                        question="What determines retrieval?",
                        plan=plan,
                        journal=journal,
                        ledger=ledger,
                        cancel_event=threading.Event(),
                        on_event=lambda e: None,
                    )
            assert requests.count(("POST", "/api/v1/documents")) == 1
            assert requests.count(("POST", "/api/chat")) == 0
            journal.close()
            journal = EffectJournal(tmp_path / "effects.sqlite", projection_ledger=ledger)
            journal.reconcile_stage_resume(
                plan.task_id, plan_version=plan.plan_version, plan_digest=plan.plan_digest
            )
        elif lost_response:
            with pytest.raises(EffectUncertainError):
                runner.run(
                    question="What determines retrieval?",
                    plan=plan,
                    journal=journal,
                    ledger=ledger,
                    cancel_event=threading.Event(),
                    on_event=lambda e: None,
                )
            before = list(requests)
            assert before[-1] == ("POST", lost_response)
            journal.close()
            journal = EffectJournal(tmp_path / "effects.sqlite", projection_ledger=ledger)
            with pytest.raises(AuthorizationError):
                journal.reconcile_stage_resume(
                    plan.task_id, plan_version=plan.plan_version, plan_digest=plan.plan_digest
                )
            with pytest.raises(EffectUncertainError):
                runner.run(
                    question="What determines retrieval?",
                    plan=plan,
                    journal=journal,
                    ledger=ledger,
                    cancel_event=threading.Event(),
                    on_event=lambda e: None,
                )
            assert requests == before
            assert journal.summary(plan.task_id)["uncertain_effects"] >= 1
            return
        result = runner.run(
            question="What determines retrieval?",
            plan=plan,
            journal=journal,
            ledger=ledger,
            cancel_event=threading.Event(),
            on_event=lambda e: None,
        )
        assert requests.count(("POST", "/api/v1/documents")) == 3
        assert len(result.papers) == len(result.claims) == 3
        assert result.execution_kind == "live" and runner.evidence_kind == "synthetic"
        assert requests.count(("POST", "/api/chat")) == 3 * question_count
        assert requests.count(("POST", "/v1/chat/completions")) == 4
        assert all(len(ids) == question_count for ids in result.claim_subquestions.values())
        assert len(result.claim_subquestions) == 3
        before = list(requests)
        repeated = runner.run(
            question="What determines retrieval?",
            plan=plan,
            journal=journal,
            ledger=ledger,
            cancel_event=threading.Event(),
            on_event=lambda e: None,
        )
        assert repeated == result
        assert requests == before
        assert journal.summary(plan.task_id)["uncertain_effects"] == 0
    finally:
        journal.close()
        ledger.close()
        resources.close()
