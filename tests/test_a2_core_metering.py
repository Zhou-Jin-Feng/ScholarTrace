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


def test_max_tokens_dialect_is_accepted_and_every_ceiling_stays_bounded(tmp_path):
    from scholartrace.delivery.effects import EffectJournal
    from scholartrace.delivery.metering import MeteredModelTransport, ModelCallPolicy

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={
            "model": "approved-model", "choices": [],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

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
            transport = MeteredModelTransport(
                inner=httpx.MockTransport(handler), journal=journal,
                task_id="task:max-tokens", policy=policy, cancel_event=threading.Event(),
            )
            async with httpx.AsyncClient(transport=transport) as http:
                # DeepSeek json_object requests carry the ceiling as ``max_tokens``.
                response = await http.post(policy.endpoint, json={
                    "model": "approved-model", "messages": [], "max_tokens": 50,
                })
                assert response.status_code == 200
            assert journal.summary("task:max-tokens")["committed_calls"] == 1

            transport = MeteredModelTransport(
                inner=httpx.MockTransport(handler), journal=journal,
                task_id="task:max-tokens", policy=policy, cancel_event=threading.Event(),
            )
            async with httpx.AsyncClient(transport=transport) as http:
                with pytest.raises(ValueError, match="output-token ceiling"):
                    await http.post(policy.endpoint, json={
                        "model": "approved-model", "messages": [], "max_tokens": 101,
                    })
                with pytest.raises(ValueError, match="output-token ceiling"):
                    await http.post(policy.endpoint, json={
                        "model": "approved-model", "messages": [],
                        "max_tokens": 10, "max_completion_tokens": 10_000,
                    })
                with pytest.raises(ValueError, match="output-token ceiling"):
                    await http.post(policy.endpoint, json={
                        "model": "approved-model", "messages": [],
                    })
            assert len(calls) == 1
        finally:
            journal.close()

    asyncio.run(run())


def test_responses_route_requires_max_output_tokens(tmp_path):
    from scholartrace.delivery.effects import EffectJournal
    from scholartrace.delivery.metering import MeteredModelTransport, ModelCallPolicy

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={
            "model": "approved-model",
            "usage": {"input_tokens": 100, "output_tokens": 20},
        })

    async def run():
        journal = EffectJournal(tmp_path / "responses.sqlite")
        policy = ModelCallPolicy(
            endpoint="https://provider.test/v1/responses",
            model="approved-model",
            input_cny_per_million=1,
            output_cny_per_million=2,
            max_input_tokens=1000,
            max_output_tokens=100,
            max_cny=0.002,
            max_calls=1,
        )
        try:
            transport = MeteredModelTransport(
                inner=httpx.MockTransport(handler), journal=journal,
                task_id="task:responses", policy=policy, cancel_event=threading.Event(),
            )
            async with httpx.AsyncClient(transport=transport) as http:
                with pytest.raises(ValueError, match="output-token ceiling"):
                    await http.post(policy.endpoint, json={
                        "model": "approved-model", "input": "synthetic",
                        "max_tokens": 50,
                    })
                response = await http.post(policy.endpoint, json={
                    "model": "approved-model", "input": "synthetic",
                    "max_output_tokens": 50,
                })
                assert response.status_code == 200
            assert len(calls) == 1
            assert journal.summary("task:responses")["committed_calls"] == 1
        finally:
            journal.close()

    asyncio.run(run())


def test_rejected_response_is_logged_with_bounded_diagnosis(tmp_path, caplog):
    import json as json_module
    import logging

    from scholartrace.delivery.effects import EffectJournal, EffectUncertainError
    from scholartrace.delivery.metering import MeteredModelTransport, ModelCallPolicy

    def handler(request):
        return httpx.Response(400, content=("\u4e2d\U0001f642" * 300_000).encode("utf-8"))

    async def run():
        journal = EffectJournal(tmp_path / "rejected.sqlite")
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
                task_id="task:rejected",
                policy=policy,
                cancel_event=threading.Event(),
            )
            async with httpx.AsyncClient(transport=transport) as http:
                with pytest.raises(EffectUncertainError):
                    await http.post(
                        policy.endpoint,
                        json={"model": "approved", "messages": [],
                              "max_completion_tokens": 10},
                    )
        finally:
            journal.close()

    with caplog.at_level(logging.WARNING, logger="scholartrace.delivery.metering"):
        asyncio.run(run())
    diagnostics = [
        json_module.loads(record.getMessage())
        for record in caplog.records
        if record.name == "scholartrace.delivery.metering"
    ]
    matched = [item for item in diagnostics
               if item.get("event") == "response_rejected_without_confirmation"]
    assert matched, diagnostics
    entry = matched[0]
    assert entry["service"] == "remote_model"
    assert entry["route"] == "/v1/chat/completions"
    assert entry["status"] == 400
    assert len(entry["body_prefix"].encode("utf-8")) <= 200
