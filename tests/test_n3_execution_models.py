import asyncio
import threading

import httpx
import pytest
from test_task_authorization import NOW, grant, phase, policy

from scholartrace.delivery.authorization import AuthorizationError
from scholartrace.delivery.effects import EffectConflictError, EffectJournal
from scholartrace.delivery.live_models import ExecutionModels
from scholartrace.workflow.storage import RuntimeLedger


@pytest.mark.parametrize("scenario", ["replay", "drift", "wrong_plan", "data_scope"])
def test_execution_model_session_binds_plan_scope_and_replay(tmp_path, scenario):
    approved = policy()
    ledger = RuntimeLedger(tmp_path / "runtime.sqlite")
    journal = EffectJournal(tmp_path / "effects.sqlite", projection_ledger=ledger)
    calls, closed = [], []

    class Transport(httpx.MockTransport):
        async def aclose(self):
            closed.append(True)
            await super().aclose()

    def respond(request):
        calls.append(True)
        return httpx.Response(
            200,
            json={
                "model": "synthetic",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                },
            },
        )

    journal.authorize_task(grant(deadline_at="2099-01-01T00:00:00+00:00"), now=NOW)
    execution = phase(
        authorization_id="execution:1",
        phase="execution",
        plan_version=1,
        plan_digest="e" * 64,
        max_remote_calls=2,
    )
    journal.prepare_phase(execution, now=NOW)
    journal.activate_phase(execution, now=NOW)
    models = ExecutionModels(
        journal=journal,
        task_id="task:synthetic",
        plan_version=1,
        plan_digest=("f" if scenario == "wrong_plan" else "e") * 64,
        api_key="x",
        cancel_event=threading.Event(),
        transport_factory=lambda: Transport(respond),
    )

    async def run():
        fields = {"claims"} if scenario == "data_scope" else {"question"}
        if scenario in {"wrong_plan", "data_scope"}:
            with pytest.raises(AuthorizationError):
                async with models.remote("verify:stable", fields):
                    raise AssertionError("must not create a session")
            assert calls == []
            return
        body = {"model": "synthetic", "max_completion_tokens": 20}
        for i in range(2):
            async with models.remote("verify:stable", fields) as (client, settings, counter):
                if scenario == "drift" and i:
                    with pytest.raises(EffectConflictError):
                        await client.post(approved.remote.endpoint, json={**body, "x": 1})
                else:
                    assert (
                        await client.post(approved.remote.endpoint, json=body)
                    ).status_code == 200
        assert calls == [True]
        assert len(closed) == 2
        assert ledger.usage("task:synthetic").api_calls == 1

    try:
        asyncio.run(run())
    finally:
        journal.close()
        ledger.close()
