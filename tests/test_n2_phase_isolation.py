from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from scholartrace.delivery.authorization import (
    AuthorizationError,
    CallContext,
    PhaseGrant,
    PlanningAuthorization,
    RuntimePolicy,
    TaskGrant,
)
from scholartrace.delivery.effects import EffectJournal
from scholartrace.delivery.metering import (
    MeteredExternalTransport,
    MeteredLocalTransport,
    MeteredModelTransport,
    ModelCallPolicy,
)


@pytest.fixture
def authorized_journal(tmp_path):
    policy = RuntimePolicy(
        remote={
            "endpoint": "https://model.invalid/v1/chat/completions",
            "model": "synthetic", "model_version": "synthetic-v1",
            "protocol": "chat_completions", "input_cny_per_million": "1",
            "output_cny_per_million": "2", "price_observed_at": datetime.now(UTC).isoformat(),
            "max_input_tokens": 1000, "max_output_tokens": 20,
        },
        local={
            "endpoint": "http://127.0.0.1:11434/api/chat",
            "model": "local-synthetic", "model_version": "synthetic-v1",
            "protocol": "ollama", "input_cny_per_million": "0",
            "output_cny_per_million": "0", "price_observed_at": datetime.now(UTC).isoformat(),
            "max_input_tokens": 1000, "max_output_tokens": 20,
        },
        data_fields=("question",), allowed_search_providers=("arxiv",), max_papers=3,
    )
    journal = EffectJournal(tmp_path / "effects.sqlite")
    journal.authorize_task(TaskGrant(
        task_id="isolation", policy=policy, max_cny="1", max_remote_calls=4,
        max_local_calls=2, max_external_requests=2,
        deadline_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    ))
    try:
        yield journal, policy
    finally:
        journal.close()


def activate(journal, policy, phase):
    # Positive non-remote limits deliberately represent pre-fix stored grants.
    grant = PhaseGrant(
        task_id="isolation", authorization_id=phase + ":1", phase=phase, generation=1,
        policy_sha256=policy.digest(), max_cny="1", max_remote_calls=4,
        max_local_calls=2, max_external_requests=2, acknowledgement_sha256="a" * 64,
        plan_version=1 if phase == "execution" else None,
        plan_digest="b" * 64 if phase == "execution" else None,
    )
    journal.prepare_phase(grant)
    journal.activate_phase(grant)
    return grant


def call_context(grant, kind):
    return CallContext(
        authorization_id=grant.authorization_id, operation_id=kind, call_kind=kind,
        policy_sha256=grant.policy_sha256, plan_version=grant.plan_version,
        plan_digest=grant.plan_digest,
    )


@pytest.mark.parametrize("kind", ["external_request", "local_model", "stage"])
def test_planning_rejects_non_remote_dispatch_even_with_stored_capacity(authorized_journal, kind):
    journal, policy = authorized_journal
    grant = activate(journal, policy, "planning")
    context = call_context(grant, kind)
    calls = []

    async def operation():
        calls.append(kind)
        return {"synthetic": True}

    with pytest.raises(AuthorizationError, match="planning"):
        asyncio.run(journal.run(
            task_id="isolation", key=context.effect_key(), request={"synthetic": True},
            operation=operation, max_cny=1, max_calls=4,
            cancel_event=threading.Event(), context=context,
        ))
    assert calls == []
    assert journal.summary("isolation")["completed_effects"] == 0
    assert journal.summary("isolation")["uncertain_effects"] == 0


def planning_payload(policy):
    return dict(
        policy_sha256=policy.digest(), max_cny="1", max_remote_calls=4,
        max_local_calls=2, max_external_requests=2, max_wall_clock_seconds=600,
        deadline_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(), generation=1,
        planning={"max_cny": "0.2", "max_remote_calls": 1,
                  "max_local_calls": 0, "max_external_requests": 0},
    )


@pytest.mark.parametrize("field", ["max_local_calls", "max_external_requests"])
def test_gate_a_payload_refuses_research_capacity(authorized_journal, field):
    _, policy = authorized_journal
    payload = planning_payload(policy)
    payload["planning"][field] = 1
    with pytest.raises(ValidationError, match="planning"):
        PlanningAuthorization.model_validate(payload)


def test_gate_a_preserves_task_capacity_for_later_execution(authorized_journal):
    _, policy = authorized_journal
    approved = PlanningAuthorization.model_validate(planning_payload(policy))
    assert approved.max_local_calls == 2
    assert approved.max_external_requests == 2
    assert approved.planning.max_local_calls == 0
    assert approved.planning.max_external_requests == 0
    assert approved.planning.max_remote_calls == 1


@pytest.mark.parametrize("phase", ["planning", "execution"])
@pytest.mark.parametrize("kind", ["remote_model", "local_model", "external_request"])
def test_transport_phase_boundary_and_successful_replay(authorized_journal, phase, kind):
    journal, policy = authorized_journal
    grant = activate(journal, policy, phase)
    context = call_context(grant, kind)
    calls = []

    def handler(request):
        calls.append(request.method)
        if kind == "remote_model":
            return httpx.Response(200, json={
                "model": "synthetic", "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            })
        if kind == "local_model":
            return httpx.Response(200, json={
                "model": "local-synthetic", "done": True, "prompt_eval_count": 10,
                "eval_count": 2, "total_duration": 1000,
            })
        return httpx.Response(200, json={"synthetic": True})

    common = dict(
        inner=httpx.MockTransport(handler), journal=journal, task_id="isolation",
        context=context, cancel_event=threading.Event(),
    )
    if kind == "remote_model":
        transport = MeteredModelTransport(**common, policy=ModelCallPolicy(
            endpoint=policy.remote.endpoint, model="synthetic", model_version="synthetic-v1",
            input_cny_per_million=1, output_cny_per_million=2,
            max_input_tokens=1000, max_output_tokens=20, max_cny=1, max_calls=4,
        ))
        method, url = "POST", policy.remote.endpoint
        payload = {"model": "synthetic", "max_completion_tokens": 20, "stream": False}
    elif kind == "local_model":
        transport = MeteredLocalTransport(**common, model_version="synthetic-v1")
        method, url = "POST", policy.local.endpoint
        payload = {"model": "local-synthetic", "stream": False, "options": {"num_predict": 20}}
    else:
        transport = MeteredExternalTransport(
            **common, service="arxiv", data_fields=frozenset({"question"}),
        )
        method, url = "GET", "https://export.arxiv.org/api/query?search_query=synthetic"
        payload = None
    allowed = phase == "execution" or kind == "remote_model"

    async def run():
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            for _ in range(2):
                if allowed:
                    assert (await client.request(method, url, json=payload)).status_code == 200
                else:
                    with pytest.raises(AuthorizationError, match="planning"):
                        await client.request(method, url, json=payload)

    asyncio.run(run())
    assert len(calls) == (1 if allowed else 0)
    summary = journal.summary("isolation")
    assert summary["committed_calls"] == int(allowed and kind == "remote_model")
    assert summary["committed_local_calls"] == int(allowed and kind == "local_model")
    assert summary["committed_external_requests"] == int(allowed and kind == "external_request")


def test_execution_stage_can_replay_without_model_or_request_charges(authorized_journal):
    journal, policy = authorized_journal
    grant = activate(journal, policy, "execution")
    context = call_context(grant, "stage")
    calls = []

    async def operation():
        calls.append(True)
        return {"synthetic": True}

    for _ in range(2):
        assert asyncio.run(journal.run(
            task_id="isolation", key=context.effect_key(), request={"synthetic": True},
            operation=operation, max_cny=1, max_calls=4,
            cancel_event=threading.Event(), context=context,
        )) == {"synthetic": True}
    assert calls == [True]
    summary = journal.summary("isolation")
    assert summary["committed_calls"] == 0
    assert summary["committed_local_calls"] == 0
    assert summary["committed_external_requests"] == 0
