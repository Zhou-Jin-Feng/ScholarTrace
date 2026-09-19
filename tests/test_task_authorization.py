from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from scholartrace.delivery.authorization import (
    AuthorizationError,
    CallContext,
    CallUsage,
    PhaseGrant,
    RuntimePolicy,
    TaskGrant,
)
from scholartrace.delivery.effects import (
    BeforeDispatchError,
    EffectConflictError,
    EffectJournal,
    EffectUncertainError,
)

NOW = datetime(2026, 9, 17, tzinfo=UTC)


def policy(**changes):
    raw = {
        "remote": {
            "endpoint": "https://model.invalid/v1/chat/completions",
            "model": "synthetic", "model_version": "synthetic-v1",
            "protocol": "chat_completions", "input_cny_per_million": "1",
            "output_cny_per_million": "2", "price_observed_at": NOW.isoformat(),
            "max_input_tokens": 100, "max_output_tokens": 20,
        },
        "data_fields": ("question",), "allowed_search_providers": ("arxiv",),
        "max_papers": 3,
    }
    raw.update(changes)
    return RuntimePolicy.model_validate(raw)


def grant(**changes):
    raw = dict(task_id="task:synthetic", policy=policy(), max_cny="1.00",
               max_remote_calls=4, max_local_calls=2, max_external_requests=10,
               deadline_at="2026-09-17T01:00:00+00:00")
    raw.update(changes)
    return TaskGrant.model_validate(raw)


def phase(**changes):
    raw = dict(task_id="task:synthetic", authorization_id="planning:1", phase="planning",
               generation=1, policy_sha256=policy().digest(), max_cny="0.5",
               max_remote_calls=2, max_local_calls=0, max_external_requests=0,
               acknowledgement_sha256="a" * 64)
    raw.update(changes)
    return PhaseGrant.model_validate(raw)


def test_phase_prepare_activate_supersede_and_revoke(tmp_path):
    journal = EffectJournal(tmp_path / "effects.sqlite")
    try:
        journal.authorize_task(grant(), now=NOW)
        with pytest.raises(AuthorizationError, match="prepared"):
            journal.activate_phase(phase(), now=NOW)
        journal.prepare_phase(phase(), now=NOW)
        state = journal.db.execute("SELECT state FROM phase_authorizations").fetchone()[0]
        assert state == "pending"
        journal.activate_phase(phase(), now=NOW)
        snapshot = tuple(journal.db.iterdump())
        journal.activate_phase(phase(), now=NOW)
        assert tuple(journal.db.iterdump()) == snapshot
        newer = phase(authorization_id="planning:2", generation=2)
        journal.prepare_phase(newer, now=NOW)
        with pytest.raises(AuthorizationError, match="newer"):
            journal.activate_phase(phase(), now=NOW)
        journal.activate_phase(newer, now=NOW)
        assert [r[0] for r in journal.db.execute(
            "SELECT state FROM phase_authorizations ORDER BY generation"
        )] == ["superseded", "active"]
        journal.revoke_task("task:synthetic", now=NOW)
        snapshot = tuple(journal.db.iterdump())
        journal.revoke_task("task:synthetic", now=NOW)
        assert tuple(journal.db.iterdump()) == snapshot
        with pytest.raises(AuthorizationError, match="revoked"):
            journal.activate_phase(newer, now=NOW)
        with pytest.raises(AuthorizationError, match="cannot change"):
            journal.authorize_task(grant(), now=NOW)
    finally:
        journal.close()


@pytest.mark.parametrize("changes", [
    {"max_cny": "1.000000000001"}, {"max_remote_calls": 5}, {"max_local_calls": 3},
    {"max_external_requests": 11}, {"policy_sha256": "b" * 64},
])
def test_phase_cannot_exceed_task_or_change_policy(tmp_path, changes):
    journal = EffectJournal(tmp_path / "effects.sqlite")
    try:
        journal.authorize_task(grant(), now=NOW)
        before = tuple(journal.db.iterdump())
        with pytest.raises(AuthorizationError):
            journal.prepare_phase(phase(**changes), now=NOW)
        assert tuple(journal.db.iterdump()) == before
    finally:
        journal.close()


def test_phase_identity_plan_and_expiry_fail_closed(tmp_path):
    with pytest.raises(ValidationError):
        phase(phase="execution")
    with pytest.raises(ValidationError):
        phase(plan_version=1, plan_digest="c" * 64)
    journal = EffectJournal(tmp_path / "effects.sqlite")
    try:
        journal.authorize_task(grant(), now=NOW)
        execution = phase(phase="execution", plan_version=1, plan_digest="c" * 64)
        journal.prepare_phase(execution, now=NOW)
        before = tuple(journal.db.iterdump())
        with pytest.raises(AuthorizationError, match="cannot change"):
            journal.activate_phase(
                phase(phase="execution", plan_version=2, plan_digest="d" * 64), now=NOW,
            )
        with pytest.raises(AuthorizationError, match="expired"):
            journal.activate_phase(execution, now=datetime(2026, 9, 18, tzinfo=UTC))
        assert tuple(journal.db.iterdump()) == before
    finally:
        journal.close()


def test_grant_idempotent_and_immutable_across_restart(tmp_path):
    path = tmp_path / "effects.sqlite"
    first = EffectJournal(path)
    first.authorize_task(grant(), now=NOW)
    first.close()
    second = EffectJournal(path)
    try:
        before = tuple(second.db.iterdump())
        second.authorize_task(grant(), now=NOW)
        assert tuple(second.db.iterdump()) == before
        with pytest.raises(AuthorizationError, match="cannot change"):
            second.authorize_task(grant(max_cny="2"), now=NOW)
        assert tuple(second.db.iterdump()) == before
    finally:
        second.close()


def test_expired_and_legacy_grants_leave_no_partial_authorization(tmp_path):
    journal = EffectJournal(tmp_path / "effects.sqlite")
    try:
        with pytest.raises(AuthorizationError, match="expired"):
            journal.authorize_task(grant(deadline_at=NOW.isoformat()), now=NOW)
        assert journal.db.execute("SELECT count(*) FROM effect_budgets").fetchone()[0] == 0
        with journal.db:
            journal.db.execute("INSERT INTO effect_budgets VALUES ('task:synthetic','1',4)")
        with pytest.raises(AuthorizationError, match="legacy"):
            journal.authorize_task(grant(), now=NOW)
        assert journal.db.execute("SELECT count(*) FROM task_authorizations").fetchone()[0] == 0
    finally:
        journal.close()


def test_legacy_run_cannot_bypass_authorized_task_phase_gate(tmp_path):
    journal = EffectJournal(tmp_path / "effects.sqlite")
    calls = []

    async def operation():
        calls.append(True)
        return {"ok": True}

    try:
        journal.authorize_task(grant(), now=NOW)
        with pytest.raises(AuthorizationError, match="context"):
            asyncio.run(journal.run(task_id="task:synthetic", key="bypass", request={},
                                    operation=operation, max_cny=1, max_calls=4,
                                    cancel_event=threading.Event()))
        assert not calls
        assert journal.db.execute("SELECT count(*) FROM effects").fetchone()[0] == 0
    finally:
        journal.close()


@pytest.mark.parametrize('changes', [
    {"max_cny": "NaN"}, {"max_cny": "-1"}, {"max_cny": True},
    {"max_remote_calls": True}, {"max_local_calls": -1},
    {"deadline_at": "2026-09-17T01:00:00"}, {"api_key": "synthetic-secret"},
])
def test_grant_rejects_invalid_values_and_secret_fields(changes):
    with pytest.raises(ValidationError):
        grant(**changes)


def test_policy_hash_is_canonical_and_endpoint_cannot_carry_credentials():
    current = policy()
    assert current.digest() == policy().digest()
    assert current.digest() != policy(data_fields=("question", "claims")).digest()
    raw = current.remote.model_dump()
    raw['endpoint'] = 'https://secret@model.invalid/v1/chat/completions'
    with pytest.raises(ValidationError):
        policy(remote=raw)
    with pytest.raises(ValidationError):
        policy(api_key='x')


def context(operation="op:1", **changes):
    raw = dict(authorization_id="planning:1", operation_id=operation,
               call_kind="remote_model", policy_sha256=policy().digest())
    raw.update(changes)
    return CallContext.model_validate(raw)


async def invoke(journal, ctx, operation, **changes):
    args = dict(task_id="task:synthetic", key=ctx.effect_key(), request={"synthetic": True},
                operation=operation, max_cny=1, max_calls=4, reserve_cny=0.2,
                reserve_calls=1, measure=lambda r: r["cost"], cancel_event=threading.Event(),
                context=ctx, now=NOW)
    args.update(changes)
    return await journal.run(**args)


def ready_journal(tmp_path):
    journal = EffectJournal(tmp_path / "effects.sqlite")
    journal.authorize_task(grant(), now=NOW)
    journal.prepare_phase(phase(), now=NOW)
    journal.activate_phase(phase(), now=NOW)
    return journal


def test_authorized_replay_identity_and_cumulative_phase_limit(tmp_path):
    journal = ready_journal(tmp_path)
    calls = []

    async def operation():
        calls.append(True)
        return {"cost": 0.2}

    try:
        asyncio.run(invoke(journal, context(), operation))
        asyncio.run(invoke(journal, context(), operation))
        assert len(calls) == 1
        with pytest.raises(EffectConflictError):
            asyncio.run(invoke(journal, context(), operation, request={"changed": True}))
        asyncio.run(invoke(journal, context("op:2"), operation))
        assert len(calls) == 2  # identical body, distinct approved operation
        new = phase(authorization_id="planning:2", generation=2)
        journal.prepare_phase(new, now=NOW)
        journal.activate_phase(new, now=NOW)
        with pytest.raises(AuthorizationError, match="capacity"):
            asyncio.run(invoke(journal, context("op:3", authorization_id="planning:2"), operation))
        assert len(calls) == 2
        assert journal.summary("task:synthetic")["committed_calls"] == 2
    finally:
        journal.close()


def test_unknown_blocks_new_attempt_operation_and_restart(tmp_path):
    journal = ready_journal(tmp_path)
    calls = []

    async def operation():
        calls.append(True)
        raise TimeoutError("synthetic timeout")

    try:
        with pytest.raises(EffectUncertainError):
            asyncio.run(invoke(journal, context(), operation))
        journal.close()
        journal = EffectJournal(tmp_path / "effects.sqlite")
        for ctx in (context(attempt=2), context("fresh-op")):
            with pytest.raises(AuthorizationError, match="uncertain"):
                asyncio.run(invoke(journal, ctx, operation))
        assert len(calls) == 1
        assert journal.summary("task:synthetic")["held_cny"] == 0.2
    finally:
        journal.close()


def test_local_external_and_stage_have_separate_counters(tmp_path):
    journal = ready_journal(tmp_path)
    execution = phase(authorization_id="execution:1", phase="execution",
                      plan_version=1, plan_digest="e" * 64,
                      max_local_calls=1, max_external_requests=3)
    journal.prepare_phase(execution, now=NOW)
    journal.activate_phase(execution, now=NOW)
    calls = []

    def execution_context(operation, kind):
        return context(operation, call_kind=kind, authorization_id="execution:1",
                       plan_version=1, plan_digest="e" * 64)

    async def operation():
        calls.append(True)
        return {"cost": 0}

    try:
        for op, kind in (("stage", "stage"), ("local", "local_model"),
                         ("external", "external_request")):
            asyncio.run(invoke(journal, execution_context(op, kind), operation,
                               reserve_cny=0, reserve_calls=0))
        with pytest.raises(AuthorizationError, match="capacity"):
            asyncio.run(invoke(journal, execution_context("local2", "local_model"), operation,
                               reserve_cny=0, reserve_calls=0))
        assert len(calls) == 3
        assert journal.summary("task:synthetic")["committed_calls"] == 0
    finally:
        journal.close()


def test_before_dispatch_retry_is_bounded_and_pending_phase_cannot_dispatch(tmp_path):
    journal = ready_journal(tmp_path)
    calls = []

    async def operation():
        calls.append(True)
        raise BeforeDispatchError("synthetic unsent request")

    try:
        for _ in range(2):
            with pytest.raises(BeforeDispatchError):
                asyncio.run(invoke(journal, context(), operation))
        with pytest.raises(EffectUncertainError, match="limit"):
            asyncio.run(invoke(journal, context(), operation))
        pending = phase(authorization_id="planning:2", generation=2)
        journal.prepare_phase(pending, now=NOW)
        with pytest.raises(AuthorizationError, match="active"):
            asyncio.run(invoke(journal, context("other", authorization_id="planning:2"), operation))
        assert len(calls) == 2
        assert journal.summary("task:synthetic")["committed_calls"] == 0
    finally:
        journal.close()


def test_context_and_intent_rollback_together(tmp_path):
    journal = ready_journal(tmp_path)
    calls = []

    async def operation():
        calls.append(True)
        return {"cost": 0.1}

    try:
        journal.db.execute(
            "CREATE TEMP TRIGGER fail_context BEFORE INSERT ON effect_contexts "
            "BEGIN SELECT RAISE(ABORT, 'synthetic context failure'); END"
        )
        import sqlite3
        with pytest.raises(sqlite3.IntegrityError, match="synthetic"):
            asyncio.run(invoke(journal, context(), operation))
        assert not calls
        assert journal.db.execute("SELECT count(*) FROM effects").fetchone()[0] == 0
        assert journal.db.execute("SELECT count(*) FROM effect_contexts").fetchone()[0] == 0
    finally:
        journal.close()


@pytest.mark.parametrize("drift", [None, "model_version", "input_cny_per_million", "endpoint"])
def test_http_context_binds_policy_and_distinct_operations(tmp_path, drift):
    from dataclasses import replace

    import httpx

    from scholartrace.delivery.metering import MeteredModelTransport, ModelCallPolicy

    journal = EffectJournal(tmp_path / "effects.sqlite")
    journal.authorize_task(grant(deadline_at="2099-01-01T00:00:00+00:00"), now=NOW)
    journal.prepare_phase(phase(), now=NOW)
    journal.activate_phase(phase(), now=NOW)
    calls = []
    model_policy = ModelCallPolicy(
        endpoint=policy().remote.endpoint, model="synthetic", model_version="synthetic-v1",
        input_cny_per_million=1, output_cny_per_million=2,
        max_input_tokens=100, max_output_tokens=20, max_cny=1, max_calls=4,
    )
    if drift is not None:
        model_policy = replace(model_policy, **{
            drift: {"model_version": "changed", "input_cny_per_million": 3,
                    "endpoint": "https://other.invalid/v1/chat/completions"}[drift],
        })

    def handler(request):
        calls.append(True)
        return httpx.Response(200, json={"model": "synthetic", "choices": [],
                                        "usage": {"prompt_tokens": 10, "completion_tokens": 2}})

    async def run():
        for op in ("op:1", "op:1", "op:2"):
            transport = MeteredModelTransport(
                inner=httpx.MockTransport(handler), journal=journal, task_id="task:synthetic",
                policy=model_policy, cancel_event=threading.Event(), context=context(op),
            )
            async with httpx.AsyncClient(transport=transport) as client:
                await client.post(model_policy.endpoint,
                                  json={"model": "synthetic", "max_completion_tokens": 20})

    try:
        if drift is None:
            asyncio.run(run())
            assert len(calls) == 2
        else:
            with pytest.raises(AuthorizationError, match="configuration"):
                asyncio.run(run())
            assert not calls
    finally:
        journal.close()


def test_known_token_overrun_persists_exposure_after_restart(tmp_path):
    import httpx

    from scholartrace.delivery.metering import MeteredModelTransport, ModelCallPolicy

    path = tmp_path / "effects.sqlite"
    journal = EffectJournal(path)
    calls = []
    model_policy = ModelCallPolicy(
        endpoint="https://model.invalid/v1/chat/completions", model="synthetic",
        input_cny_per_million=1, output_cny_per_million=2,
        max_input_tokens=100, max_output_tokens=20, max_cny=1, max_calls=4,
    )

    def handler(request):
        calls.append(True)
        return httpx.Response(200, json={"model": "synthetic",
                                        "usage": {"prompt_tokens": 1000, "completion_tokens": 20}})

    async def run():
        transport = MeteredModelTransport(
            inner=httpx.MockTransport(handler), journal=journal, task_id="legacy:overrun",
            policy=model_policy, cancel_event=threading.Event(),
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(EffectUncertainError):
                await client.post(model_policy.endpoint,
                                  json={"model": "synthetic", "max_completion_tokens": 20})

    try:
        asyncio.run(run())
        journal.close()
        journal = EffectJournal(path)
        asyncio.run(run())
        assert len(calls) == 1
        assert journal.summary("legacy:overrun")["held_cny"] == pytest.approx(0.00104)
    finally:
        journal.close()


def test_two_connections_cannot_spend_the_last_phase_slot_twice(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    journal = EffectJournal(tmp_path / "effects.sqlite")
    journal.authorize_task(grant(), now=NOW)
    only_one = phase(max_remote_calls=1)
    journal.prepare_phase(only_one, now=NOW)
    journal.activate_phase(only_one, now=NOW)
    other = EffectJournal(tmp_path / "effects.sqlite")
    barrier = threading.Barrier(2)
    calls = []

    async def operation():
        calls.append(True)
        return {"cost": 0.1}

    def compete(args):
        connection, op = args
        barrier.wait(timeout=5)
        try:
            asyncio.run(invoke(connection, context(op), operation))
            return "completed"
        except AuthorizationError:
            return "refused"

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(compete, [(journal, "one"), (other, "two")]))
        assert sorted(outcomes) == ["completed", "refused"]
        assert len(calls) == 1
    finally:
        other.close()
        journal.close()


def test_execution_uses_same_task_budget_after_planning(tmp_path):
    journal = ready_journal(tmp_path)

    async def operation():
        return {"cost": 0.4}

    try:
        asyncio.run(invoke(journal, context(), operation, reserve_cny=0.4))
        execution = phase(authorization_id="execution:1", phase="execution", max_cny="1",
                          plan_version=1, plan_digest="e" * 64)
        journal.prepare_phase(execution, now=NOW)
        journal.activate_phase(execution, now=NOW)
        ctx = context("execute:1", authorization_id="execution:1",
                      plan_version=1, plan_digest="e" * 64)
        with pytest.raises(AuthorizationError, match="capacity"):
            asyncio.run(invoke(journal, ctx, operation, reserve_cny=0.7))
        asyncio.run(invoke(journal, ctx, operation, reserve_cny=0.6))
        assert journal.summary("task:synthetic")["committed_cny"] == 0.8
        assert journal.db.execute("SELECT count(*) FROM effect_budgets").fetchone()[0] == 1
    finally:
        journal.close()


def test_confirmed_second_attempt_is_charged_and_bool_attempt_refused(tmp_path):
    journal = ready_journal(tmp_path)
    calls = []

    async def operation():
        calls.append(True)
        return {"cost": 0.1, "business_result": "invalid structured output"}

    try:
        with pytest.raises(ValidationError):
            context(attempt=True)
        with pytest.raises(AuthorizationError, match="confirmed"):
            asyncio.run(invoke(journal, context(attempt=2), operation))
        asyncio.run(invoke(journal, context(), operation))
        asyncio.run(invoke(journal, context(attempt=2), operation))
        asyncio.run(invoke(journal, context(attempt=2), operation))
        assert len(calls) == 2
        assert journal.summary("task:synthetic")["committed_calls"] == 2
    finally:
        journal.close()


@pytest.mark.parametrize("fault", [None, "timeout", "missing_usage", "overrun", "wrong_model"])
def test_local_transport_counts_and_durable_usage(tmp_path, fault):
    import httpx

    from scholartrace.delivery.metering import MeteredLocalTransport

    local = dict(endpoint="http://127.0.0.1:11434/api/chat", model="local-synthetic",
                 model_version="fixture-v1", protocol="ollama", input_cny_per_million="0",
                 output_cny_per_million="0", max_input_tokens=1000, max_output_tokens=20,
                 price_observed_at=NOW.isoformat())
    approved = policy(local=local)
    journal = EffectJournal(tmp_path / "effects.sqlite")
    journal.authorize_task(grant(policy=approved, deadline_at="2099-01-01T00:00:00+00:00"), now=NOW)
    execution = phase(policy_sha256=approved.digest(), authorization_id="execution:1",
                      phase="execution", plan_version=1, plan_digest="e" * 64,
                      max_local_calls=1)
    journal.prepare_phase(execution, now=NOW)
    journal.activate_phase(execution, now=NOW)
    ctx = context(call_kind="local_model", policy_sha256=approved.digest(),
                  authorization_id="execution:1", plan_version=1, plan_digest="e" * 64)
    calls = []

    def handler(request):
        calls.append(True)
        if fault == "timeout":
            raise httpx.ReadTimeout("synthetic")
        envelope = dict(model="local-synthetic", done=True, prompt_eval_count=100,
                        eval_count=10, total_duration=5000, message={"content": "synthetic"})
        if fault == "missing_usage":
            envelope.pop("eval_count")
        if fault == "overrun":
            envelope["eval_count"] = 30
        if fault == "wrong_model":
            envelope["model"] = "another"
        return httpx.Response(200, json=envelope)

    async def run():
        transport = MeteredLocalTransport(
            inner=httpx.MockTransport(handler), journal=journal, task_id="task:synthetic",
            context=ctx, model_version="fixture-v1", cancel_event=threading.Event(),
        )
        async with httpx.AsyncClient(transport=transport) as client:
            for _ in range(2):
                if fault is None:
                    response = await client.post(local["endpoint"], json={
                        "model": "local-synthetic", "stream": False, "options": {"num_predict": 20},
                    })
                    assert response.status_code == 200
                else:
                    with pytest.raises(EffectUncertainError):
                        await client.post(local["endpoint"], json={
                            "model": "local-synthetic", "stream": False,
                            "options": {"num_predict": 20},
                        })

    try:
        asyncio.run(run())
        assert len(calls) == 1
        summary = journal.summary("task:synthetic")
        assert summary["committed_calls"] == 0
        row = journal.db.execute("SELECT * FROM effect_contexts").fetchone()
        assert row["reserve_local_calls"] == 1
        if fault in {None, "overrun"}:
            import json
            usage = json.loads(row["usage_json"])
            assert usage["input_tokens"] == 100
            assert usage["output_tokens"] == (30 if fault == "overrun" else 10)
        else:
            assert row["usage_json"] is None
    finally:
        journal.close()


@pytest.mark.parametrize("scenario", ["search", "retrieve", "redirect", "wrong_origin",
                                     "denied_provider", "denied_field", "ingest_without_pdf"])
def test_external_transport_scope_replay_and_unknown(tmp_path, scenario):
    import httpx

    from scholartrace.delivery.metering import MeteredExternalTransport

    approved = policy(documind_url="http://127.0.0.1:8010")
    journal = EffectJournal(tmp_path / "effects.sqlite")
    journal.authorize_task(grant(policy=approved, deadline_at="2099-01-01T00:00:00+00:00"), now=NOW)
    execution = phase(policy_sha256=approved.digest(), authorization_id="execution:1",
                      phase="execution", plan_version=1, plan_digest="e" * 64,
                      max_external_requests=3)
    journal.prepare_phase(execution, now=NOW)
    journal.activate_phase(execution, now=NOW)
    ctx = context(call_kind="external_request", policy_sha256=approved.digest(),
                  authorization_id="execution:1", plan_version=1, plan_digest="e" * 64)
    calls = []
    service, method, url = "arxiv", "GET", "https://export.arxiv.org/api/query?search_query=synthetic"
    fields = frozenset({"question"})
    if scenario in {"retrieve", "ingest_without_pdf"}:
        service, method, url = "documind", "POST", "http://127.0.0.1:8010/api/v1/retrieve"
        if scenario == "ingest_without_pdf":
            url = "http://127.0.0.1:8010/api/v1/documents"
    if scenario == "wrong_origin":
        url = "https://unapproved.invalid/api/query"
    if scenario == "denied_provider":
        service, url = "crossref", "https://api.crossref.org/works"
    if scenario == "denied_field":
        fields = frozenset({"selected_pdf"})

    def handler(request):
        calls.append(True)
        if scenario == "redirect":
            return httpx.Response(302, headers={"location": "https://unapproved.invalid"})
        return httpx.Response(200, json={"synthetic": True})

    async def run():
        transport = MeteredExternalTransport(
            inner=httpx.MockTransport(handler), journal=journal, task_id="task:synthetic",
            context=ctx, service=service, data_fields=fields, cancel_event=threading.Event(),
        )
        async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
            for _ in range(2):
                if scenario in {"search", "retrieve"}:
                    result = await client.request(method, url)
                    assert result.json() == {"synthetic": True}
                elif scenario == "redirect":
                    with pytest.raises(EffectUncertainError):
                        await client.request(method, url)
                else:
                    with pytest.raises(AuthorizationError):
                        await client.request(method, url)

    try:
        asyncio.run(run())
        assert len(calls) == (1 if scenario in {"search", "retrieve", "redirect"} else 0)
        assert journal.summary("task:synthetic")["committed_calls"] == 0
        if calls:
            row = journal.db.execute("SELECT * FROM effect_contexts").fetchone()
            assert row["reserve_external_requests"] == 1
            assert row["reserve_local_calls"] == 0
    finally:
        journal.close()


def test_projection_failure_recovers_without_reissuing_or_double_counting(tmp_path, monkeypatch):
    from scholartrace.workflow.storage import RuntimeLedger

    journal = ready_journal(tmp_path)
    ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
    calls = []

    async def operation():
        calls.append(True)
        return {"cost": 0.1}

    def broken(**kwargs):
        raise OSError("synthetic projection failure")

    try:
        asyncio.run(invoke(journal, context(), operation,
                           usage=lambda r: CallUsage(input_tokens=10, output_tokens=2)))
        with monkeypatch.context() as patch:
            patch.setattr(ledger, "project_confirmed_usage", broken)
            report = journal.project_usage("task:synthetic", ledger)
            assert report["state"] == "pending"
            assert report["authoritative"]["measured_cny"] == 0.1
            assert ledger.usage("task:synthetic").external_cost_cny == 0
        journal.close()
        journal = EffectJournal(tmp_path / "effects.sqlite")
        for _ in range(2):
            assert journal.project_usage("task:synthetic", ledger)["state"] == "synced"
        assert ledger.usage("task:synthetic").external_cost_cny == 0.1
        assert ledger.usage("task:synthetic").llm_input_tokens == 10
        assert ledger.usage("task:synthetic").api_calls == 1
        asyncio.run(invoke(journal, context(), operation))
        assert len(calls) == 1
    finally:
        ledger.close()
        journal.close()


def test_unknown_usage_is_not_projected_as_zero_or_reported_synced(tmp_path):
    from scholartrace.workflow.storage import RuntimeLedger

    journal = ready_journal(tmp_path)
    ledger = RuntimeLedger(tmp_path / "runtime.sqlite")

    async def operation():
        raise TimeoutError("synthetic")

    try:
        with pytest.raises(EffectUncertainError):
            asyncio.run(invoke(journal, context(), operation))
        report = journal.project_usage("task:synthetic", ledger)
        assert report["state"] == "reconciliation_required"
        assert report["projected_effects"] == 0
        assert report["authoritative"]["held_cny"] == 0.2
    finally:
        ledger.close()
        journal.close()


def test_plan_binding_preserves_task_capacity_and_outer_rollback(tmp_path):
    from decimal import Decimal

    from scholartrace.delivery.budget import BudgetError, BudgetReservationStore
    from scholartrace.delivery.store import DeliveryStore
    from scholartrace.delivery.transactions import transaction

    delivery = DeliveryStore(tmp_path / "tasks.sqlite")
    store = BudgetReservationStore(delivery.connection, delivery._lock)
    original = store.reserve(
        task_id="task:synthetic", plan_version=0, plan_digest="planning:" + policy().digest(),
        reserved_cny=1, reserved_api_calls=4, reserved_wall_clock_seconds=60,
        estimate_source="authorized:" + policy().digest(),
    )
    args = dict(task_id="task:synthetic", policy_sha256=policy().digest(), plan_version=1,
                plan_digest="f" * 64, expected_cny=Decimal("1"), expected_api_calls=4,
                expected_wall_clock_seconds=60)
    try:
        with (
            pytest.raises(RuntimeError, match="synthetic audit"),
            transaction(delivery.connection),
        ):
            store.bind_authorized_plan(**args)
            raise RuntimeError("synthetic audit failure")
        assert store.get("task:synthetic") == original
        bound = store.bind_authorized_plan(**args)
        assert store.bind_authorized_plan(**args) == bound
        assert bound.reserved_at == original.reserved_at
        assert bound.reserved_cny == original.reserved_cny
        assert store.committed_cny() == 1
        with pytest.raises(BudgetError):
            store.bind_authorized_plan(**(args | {"expected_cny": Decimal("2")}))
        with pytest.raises(BudgetError):
            store.bind_authorized_plan(**(args | {"plan_digest": "e" * 64}))
        assert store.get("task:synthetic") == bound
    finally:
        delivery.close()


def test_stopped_backup_restores_grants_identity_and_unknown_exposure(tmp_path):
    from scholartrace.operations.backup import backup_data, restore_data

    source = tmp_path / "source"
    source.mkdir()
    journal = ready_journal(source)

    async def uncertain():
        raise TimeoutError("synthetic")

    with pytest.raises(EffectUncertainError):
        asyncio.run(invoke(journal, context(), uncertain))
    original = tuple(journal.db.iterdump())
    journal.close()
    archive = tmp_path / "backup.zip"
    backup_data(data_dir=source, output_path=archive)
    restored = tmp_path / "restored"
    result = restore_data(archive_path=archive, target_dir=restored)
    assert result["verified"] is True
    recovered = EffectJournal(restored / "effects.sqlite")
    try:
        assert tuple(recovered.db.iterdump()) == original
        assert recovered.summary("task:synthetic")["held_cny"] == 0.2
        with pytest.raises(AuthorizationError, match="uncertain"):
            asyncio.run(invoke(recovered, context("changed-identity"), uncertain))
    finally:
        recovered.close()


@pytest.mark.parametrize("nested", [False, True])
def test_stale_restore_fences_fresh_calls_after_missing_exposure(tmp_path, nested):
    from scholartrace.operations.backup import backup_data, restore_data

    source = tmp_path / "source"
    relative = "workflow/nested" if nested else "."
    journal = ready_journal(source / relative)
    snapshot = tuple(journal.db.iterdump())
    journal.close()
    archive = tmp_path / "stale.zip"
    backup_data(data_dir=source, output_path=archive)
    calls = []

    async def uncertain():
        calls.append("post-backup exposure")
        raise TimeoutError("synthetic external outcome unknown")

    journal = EffectJournal(source / relative / "effects.sqlite")
    try:
        with pytest.raises(EffectUncertainError):
            asyncio.run(invoke(journal, context("later"), uncertain))
        assert journal.summary("task:synthetic")["held_cny"] == 0.2
    finally:
        journal.close()

    restored = tmp_path / "restored"
    restore_data(archive_path=archive, target_dir=restored)

    async def forbidden():
        calls.append("unsafe fresh dispatch")
        return {"cost": 0.1}

    for _ in range(2):
        recovered = EffectJournal(restored / relative / "effects.sqlite")
        try:
            assert tuple(recovered.db.iterdump()) == snapshot
            assert recovered.summary("task:synthetic")["held_cny"] == 0
            with pytest.raises(AuthorizationError, match="restore.*reconciliation"):
                asyncio.run(invoke(recovered, context("fresh"), forbidden))
            assert calls == ["post-backup exposure"]
            assert tuple(recovered.db.iterdump()) == snapshot
        finally:
            recovered.close()


def test_restore_fences_activation_but_allows_reads_and_revocation(tmp_path):
    from scholartrace.operations.backup import backup_data, restore_data

    source = tmp_path / "source"
    journal = ready_journal(source)
    pending = phase(authorization_id="execution:1", phase="execution",
                    plan_version=1, plan_digest="e" * 64)
    journal.prepare_phase(pending, now=NOW)
    snapshot = tuple(journal.db.iterdump())
    journal.close()
    archive = tmp_path / "backup.zip"
    backup_data(data_dir=source, output_path=archive)
    restored = tmp_path / "restored"
    restore_data(archive_path=archive, target_dir=restored)
    journal = EffectJournal(restored / "effects.sqlite")
    try:
        for candidate in (phase(), pending):
            with pytest.raises(AuthorizationError, match="restore.*reconciliation"):
                journal.activate_phase(candidate, now=NOW)
        assert tuple(journal.db.iterdump()) == snapshot
        assert journal.has_authorization("task:synthetic")
        assert journal.summary("task:synthetic")["held_cny"] == 0
        journal.revoke_phase("task:synthetic", "execution")
        journal.revoke_task("task:synthetic", now=NOW)
        state = journal.db.execute("SELECT state FROM task_authorizations").fetchone()[0]
        assert state == "revoked"
        assert {r[0] for r in journal.db.execute("SELECT state FROM phase_authorizations")} == {
            "revoked",
        }
    finally:
        journal.close()


def test_restore_of_restored_legacy_data_replay_and_tree_scope(tmp_path):
    from scholartrace.operations.backup import backup_data, restore_data

    source = tmp_path / "source"
    calls = []

    async def operation():
        calls.append(True)
        return {"cost": 0.1}

    def legacy(journal, key):
        return invoke(journal, context(), operation, task_id="legacy", context=None, key=key)

    journal = EffectJournal(source / "nested" / "effects.sqlite")
    try:
        asyncio.run(legacy(journal, "completed"))
        snapshot = tuple(journal.db.iterdump())
    finally:
        journal.close()
    for generation in range(2):
        archive = tmp_path / f"backup-{generation}.zip"
        backup_data(data_dir=source, output_path=archive)
        restored = tmp_path / f"restored-{generation}"
        restore_data(archive_path=archive, target_dir=restored)
        for _ in range(2):
            journal = EffectJournal(restored / "nested" / "effects.sqlite")
            try:
                assert asyncio.run(legacy(journal, "completed")) == {"cost": 0.1}
                with pytest.raises(AuthorizationError, match="restore.*reconciliation"):
                    asyncio.run(legacy(journal, "fresh"))
                assert tuple(journal.db.iterdump()) == snapshot
                assert calls == [True]
            finally:
                journal.close()
        # A fresh database within the restored tree cannot bypass the tree fence.
        journal = EffectJournal(restored / "new" / "effects.sqlite")
        try:
            with pytest.raises(AuthorizationError, match="restore.*reconciliation"):
                asyncio.run(legacy(journal, "fresh-database"))
            assert journal.db.execute("SELECT count(*) FROM effect_budgets").fetchone()[0] == 0
        finally:
            journal.close()
        source = restored
    # The shared parent of restores is never fenced; a fresh sibling works normally.
    journal = ready_journal(tmp_path / "fresh-sibling")
    try:
        asyncio.run(invoke(journal, context(), operation))
        assert calls == [True, True]
    finally:
        journal.close()


@pytest.mark.parametrize("fail_flush", [False, True])
def test_restore_marker_is_flushed_before_publish_or_restore_fails(
    tmp_path, monkeypatch, fail_flush,
):
    from scholartrace import restore_fence
    from scholartrace.operations import backup

    source = tmp_path / "source"
    journal = ready_journal(source)
    journal.close()
    archive = tmp_path / "backup.zip"
    backup.backup_data(data_dir=source, output_path=archive)
    restored = tmp_path / "restored"
    real_fsync, real_replace = restore_fence.os.fsync, backup.os.replace
    events = []

    def fsync(fd):
        events.append("flush")
        if fail_flush:
            raise OSError("synthetic fence flush failure")
        real_fsync(fd)

    def publish(staging, destination):
        assert (staging / restore_fence.RESTORE_FENCE_NAME).is_file()
        assert events == ["flush"]
        events.append("publish")
        real_replace(staging, destination)

    monkeypatch.setattr(restore_fence.os, "fsync", fsync)
    monkeypatch.setattr(backup.os, "replace", publish)
    if fail_flush:
        with pytest.raises(backup.BackupError, match="restore failed"):
            backup.restore_data(archive_path=archive, target_dir=restored)
        assert not restored.exists()
        assert not list(tmp_path.glob(".restored.restore-*"))
        assert events == ["flush"]
    else:
        backup.restore_data(archive_path=archive, target_dir=restored)
        assert events == ["flush", "publish"]


@pytest.mark.parametrize("marker_state", ["empty", "directory", "inaccessible"])
def test_open_journal_checks_fence_at_admission_and_fails_closed(
    tmp_path, monkeypatch, marker_state,
):
    from pathlib import Path

    from scholartrace.restore_fence import RESTORE_FENCE_NAME

    journal = ready_journal(tmp_path / "nested")
    marker = tmp_path / RESTORE_FENCE_NAME
    if marker_state == "directory":
        marker.mkdir()
    else:
        marker.touch()
    if marker_state == "inaccessible":
        original = Path.lstat

        def lstat(path, *args, **kwargs):
            if path == marker:
                raise PermissionError("synthetic unreadable fence")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", lstat)
    calls = []

    async def forbidden():
        calls.append(True)
        return {"cost": 0.1}

    try:
        snapshot = tuple(journal.db.iterdump())
        with pytest.raises(AuthorizationError, match="restore.*reconciliation"):
            asyncio.run(invoke(journal, context(), forbidden))
        with pytest.raises(AuthorizationError, match="restore.*reconciliation"):
            journal.activate_phase(phase(), now=NOW)
        assert not calls
        assert tuple(journal.db.iterdump()) == snapshot
    finally:
        journal.close()

def test_local_transport_admits_one_structured_repair_attempt(tmp_path):
    import json

    import httpx

    from scholartrace.delivery.metering import MeteredLocalTransport

    local = dict(endpoint="http://127.0.0.1:11434/api/chat", model="local-synthetic",
                 model_version="fixture-v1", protocol="ollama", input_cny_per_million="0",
                 output_cny_per_million="0", max_input_tokens=1000, max_output_tokens=20,
                 price_observed_at=NOW.isoformat())
    approved = policy(local=local)
    journal = EffectJournal(tmp_path / "effects.sqlite")
    journal.authorize_task(grant(policy=approved, deadline_at="2099-01-01T00:00:00+00:00"), now=NOW)
    execution = phase(policy_sha256=approved.digest(), authorization_id="execution:1",
                      phase="execution", plan_version=1, plan_digest="e" * 64,
                      max_local_calls=2)
    journal.prepare_phase(execution, now=NOW)
    journal.activate_phase(execution, now=NOW)
    ctx = context(call_kind="local_model", policy_sha256=approved.digest(),
                  authorization_id="execution:1", plan_version=1, plan_digest="e" * 64)
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={
            "model": "local-synthetic", "done": True,
            "prompt_eval_count": 100, "eval_count": 10,
        })

    async def run():
        transport = MeteredLocalTransport(
            inner=httpx.MockTransport(handler), journal=journal, task_id="task:synthetic",
            context=ctx, model_version="fixture-v1", cancel_event=threading.Event(),
        )
        base = {"model": "local-synthetic", "stream": False, "options": {"num_predict": 20}}
        async with httpx.AsyncClient(transport=transport) as client:
            first = await client.post(local["endpoint"], json=base)
            assert first.status_code == 200
            repair = dict(base, messages=[{"role": "system", "content": "correction"}])
            second = await client.post(local["endpoint"], json=repair)
            assert second.status_code == 200

    try:
        asyncio.run(run())
        assert len(bodies) == 2
        attempts = [row[0] for row in journal.db.execute(
            "SELECT attempt FROM effect_contexts ORDER BY attempt"
        ).fetchall()]
        assert attempts == [1, 2]
    finally:
        journal.close()
