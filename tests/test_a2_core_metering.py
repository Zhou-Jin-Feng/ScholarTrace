from __future__ import annotations

import asyncio
import threading

import httpx
import pytest


def test_actual_http_dispatch_is_reserved_metered_and_replayed_offline(tmp_path):
    from scholartrace.delivery.effects import EffectJournal
    from scholartrace.delivery.metering import MeteredModelTransport, ModelCallPolicy

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "model": "approved-model",
                "choices": [],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )

    async def run():
        journal = EffectJournal(tmp_path / "effects.sqlite")
        policy = ModelCallPolicy(
            endpoint="https://provider.test/v1/chat/completions",
            model="approved-model",
            input_cny_per_million=1,
            output_cny_per_million=2,
            max_input_tokens=1000,
            max_output_tokens=100,
            max_cny=0.002,
            max_calls=1,
        )
        try:
            for _ in range(2):
                transport = MeteredModelTransport(
                    inner=httpx.MockTransport(handler),
                    journal=journal,
                    task_id="task:meter",
                    policy=policy,
                    cancel_event=threading.Event(),
                )
                async with httpx.AsyncClient(transport=transport) as http:
                    response = await http.post(
                        policy.endpoint,
                        json={
                            "model": "approved-model",
                            "messages": [{"role": "user", "content": "synthetic input"}],
                            "max_completion_tokens": 100,
                        },
                    )
                    assert response.status_code == 200
            assert len(calls) == 1
            assert journal.summary("task:meter")["measured_cny"] == pytest.approx(0.00014)
            assert journal.summary("task:meter")["committed_calls"] == 1
        finally:
            journal.close()

    asyncio.run(run())


def test_existing_strong_report_adapter_uses_the_durable_http_meter(tmp_path):
    from test_api_strong_report_generator import _draft, _generator, _request, _response

    from scholartrace.delivery.effects import EffectJournal
    from scholartrace.delivery.metering import MeteredModelTransport, ModelCallPolicy

    calls = []

    def handler(request):
        calls.append(request)
        return _response(_draft())

    async def run():
        journal = EffectJournal(tmp_path / "meter.sqlite")
        policy = ModelCallPolicy(
            endpoint="https://provider.test/v1/responses",
            model="gpt-5.6-terra",
            input_cny_per_million=1,
            output_cny_per_million=2,
            max_input_tokens=30000,
            max_output_tokens=1200,
            max_cny=0.04,
            max_calls=1,
        )
        try:
            for _ in range(2):
                transport = MeteredModelTransport(
                    inner=httpx.MockTransport(handler),
                    journal=journal,
                    task_id="task:adapter",
                    policy=policy,
                    cancel_event=threading.Event(),
                )
                async with httpx.AsyncClient(transport=transport) as http:
                    result = await _generator(http).generate(_request())
                    assert result.status == "succeeded"
            assert len(calls) == 1
            assert journal.summary("task:adapter")["measured_cny"] == pytest.approx(0.00134)
        finally:
            journal.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault", ["timeout", "missing_usage", "redirect", "wrong_model", "bad_tokens"]
)
def test_unknown_http_outcome_holds_budget_and_does_not_retry(tmp_path, fault):
    from scholartrace.delivery.effects import EffectJournal, EffectUncertainError
    from scholartrace.delivery.metering import MeteredModelTransport, ModelCallPolicy

    calls = []

    def handler(request):
        calls.append(1)
        if fault == "timeout":
            raise httpx.ReadTimeout("synthetic timeout")
        if fault == "redirect":
            return httpx.Response(302, headers={"location": "https://unexpected.test"})
        data = {"model": "approved", "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
        if fault == "missing_usage":
            data.pop("usage")
        if fault == "wrong_model":
            data["model"] = "unexpected"
        if fault == "bad_tokens":
            data["usage"]["prompt_tokens"] = True
        return httpx.Response(200, json=data)

    async def run():
        journal = EffectJournal(tmp_path / "calls.sqlite")
        policy = ModelCallPolicy(
            endpoint="https://provider.test/v1/chat/completions",
            model="approved",
            input_cny_per_million=1,
            output_cny_per_million=1,
            max_input_tokens=1000,
            max_output_tokens=100,
            max_cny=0.01,
            max_calls=2,
        )
        try:
            transport = MeteredModelTransport(
                inner=httpx.MockTransport(handler),
                journal=journal,
                task_id="task:fault",
                policy=policy,
                cancel_event=threading.Event(),
            )
            async with httpx.AsyncClient(transport=transport) as http:
                body = {"model": "approved", "messages": [], "max_completion_tokens": 10}
                with pytest.raises((EffectUncertainError, httpx.ReadTimeout)):
                    await http.post(policy.endpoint, json=body)
                with pytest.raises(EffectUncertainError):
                    await http.post(policy.endpoint, json=body)
            assert len(calls) == 1
            assert journal.summary("task:fault")["held_cny"] == pytest.approx(0.0011)
            assert journal.summary("task:fault")["uncertain_effects"] == 1
        finally:
            journal.close()

    asyncio.run(run())
