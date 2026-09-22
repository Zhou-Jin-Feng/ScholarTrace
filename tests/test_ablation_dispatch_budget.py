from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from sa04_synthetic import make_synthetic_inputs
from test_sa05_formal_checkpoint import _manifest

from scholartrace.model_provider.plan_generator import ApiCallBudget, ApiCallCounter
from scholartrace.model_provider.settings import ProviderSettings
from scholartrace.verification_ablation import (
    AblationReportRequest,
    DeterministicFixtureAblationReportGenerator,
    DeterministicFixtureAblationVerifier,
    VerificationAblationRunner,
)
from scholartrace.verification_ablation.provider import OpenAICompatibleAblationReportGenerator


class CountingReporter(DeterministicFixtureAblationReportGenerator):
    def __init__(self) -> None:
        self.dispatches = 0

    async def generate(self, request: AblationReportRequest):
        self.dispatches += 1
        return await super().generate(request)


def make_runner(reporter):
    return VerificationAblationRunner(
        report_generator=reporter,
        verifier_backend=DeterministicFixtureAblationVerifier(),
        allow_fixture=True,
    )


def test_zero_report_budget_does_not_dispatch() -> None:
    inputs = make_synthetic_inputs()
    manifest = _manifest(inputs)
    manifest = manifest.model_copy(
        update={
            "budget": manifest.budget.model_copy(update={"max_report_calls": 0}),
        }
    )
    reporter = CountingReporter()
    archive = asyncio.run(
        make_runner(reporter).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:zero-budget",
        )
    )
    assert reporter.dispatches == 0
    assert all(row.result.status == "failed" for row in archive.rows)
    assert all(a.attempted_calls == 0 for a in archive.attempts if a.operation == "report")


def test_shared_report_limit_stops_second_condition_before_dispatch() -> None:
    inputs = make_synthetic_inputs()
    manifest = _manifest(inputs)
    manifest = manifest.model_copy(
        update={
            "budget": manifest.budget.model_copy(
                update={
                    "budget_scope": "shared",
                    "max_report_calls": 1,
                }
            )
        }
    )
    reporter = CountingReporter()
    archive = asyncio.run(
        make_runner(reporter).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:shared-budget",
        )
    )
    assert reporter.dispatches == 1
    assert sum(u.report_calls for u in archive.usage_by_variant.values()) == 1


@pytest.mark.parametrize("cost_cap,expected_dispatches", [(1.0, 2), (0.5, 0)])
def test_retry_keeps_history_in_budget_and_usage(cost_cap, expected_dispatches) -> None:
    inputs = make_synthetic_inputs()
    manifest = _manifest(inputs)
    manifest = manifest.model_copy(
        update={
            "budget": manifest.budget.model_copy(
                update={
                    "budget_scope": "shared",
                    "max_report_calls": 3,
                    "max_reference_cost_cny": cost_cap,
                    "max_input_tokens": 1000,
                    "max_output_tokens": 1000,
                }
            )
        }
    )
    initial = asyncio.run(
        make_runner(CountingReporter()).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:retry-budget",
        )
    )
    old = next(a for a in initial.attempts if a.operation == "report" and a.variant == "V-off")
    failed = old.model_copy(
        update={
            "state": "failed",
            "generated_report": None,
            "attempted_calls": 1,
            "successful_calls": 0,
            "actual_reference_cost_cny": 0.75,
            "input_tokens": 100,
            "output_tokens": 50,
        }
    )
    reporter = CountingReporter()
    archive = asyncio.run(
        make_runner(reporter).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:retry-budget",
            existing_attempts=[failed],
            retry_unknown=True,
        )
    )
    assert reporter.dispatches == expected_dispatches
    assert sum(u.reference_cost_cny or 0 for u in archive.usage_by_variant.values()) == 0.75
    assert sum(u.report_calls for u in archive.usage_by_variant.values()) == 1 + expected_dispatches
    assert any(a.attempt_id == failed.attempt_id for a in archive.attempts)


def test_real_report_adapter_has_shared_pre_send_cost_reservation() -> None:
    inputs = make_synthetic_inputs()
    # Reuse completed verifier results so only reports can make HTTP requests.
    initial = asyncio.run(
        make_runner(CountingReporter()).run(
            manifest=_manifest(inputs),
            inputs=inputs,
            run_id="run:cache-source",
        )
    )
    cached = {
        r.result.question_id: r.verifications for r in initial.rows if r.result.variant == "V-on"
    }
    dispatches = []

    def handle(request: httpx.Request) -> httpx.Response:
        dispatches.append(request)
        payload = json.loads(request.content)
        packet = json.loads(payload["input"][1]["content"])
        context = json.loads(packet["prepared_context"])
        claim = context["claims"][0]
        draft = {
            "title": "Synthetic report",
            "answer_status": "answered",
            "findings": [
                {
                    "claim_id": claim["claim_id"],
                    "claim": claim["text"],
                    "evidence_ids": claim["evidence_ids"],
                    "support_status": claim["prepared_semantic_status"],
                }
            ],
            "limitations": ["Synthetic evidence only."],
        }
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "status": "completed",
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(draft),
                            }
                        ],
                    }
                ],
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            reporter = OpenAICompatibleAblationReportGenerator(
                client=client,
                settings=ProviderSettings(
                    base_url="https://provider.test/v1",
                    api_key="fake",
                    timeout_seconds=5,
                ),
                model_profile="test",
                model="test-model",
                protocol="responses",
                call_counter=ApiCallCounter(
                    ApiCallBudget(
                        max_calls=2,
                        max_input_token_upper_bound=20000,
                        max_output_tokens=1200,
                        max_cost_cny=10,
                    )
                ),
            )
            manifest = _manifest(inputs).model_copy(
                update={
                    "execution_mode": "production",
                    "model_profile": reporter.model_profile,
                    "model_identifier": reporter.model_identifier,
                    "provider_protocol": "responses",
                    "report_prompt_sha256": reporter.prompt_template_sha256,
                    "report_schema_sha256": reporter.report_schema_sha256,
                }
            )
            # Reserve = 0.408 CNY; after first actual 0.006 CNY, the next reserve exceeds 0.410.
            manifest = manifest.model_copy(
                update={
                    "budget": manifest.budget.model_copy(
                        update={
                            "budget_scope": "shared",
                            "max_reference_cost_cny": 0.410,
                            "max_input_tokens": 100000,
                            "max_output_tokens": 100000,
                            "max_model_calls": 2,
                            "max_provider_api_calls": 2,
                        }
                    )
                }
            )
            return await make_runner(reporter).run(
                manifest=manifest,
                inputs=inputs,
                run_id="run:real-reservation",
                existing_verifications=cached,
            )

    archive = asyncio.run(run())
    assert len(dispatches) == 1
    assert sum(u.report_calls for u in archive.usage_by_variant.values()) == 1
    assert sum(u.reference_cost_cny or 0 for u in archive.usage_by_variant.values()) > 0


@pytest.mark.parametrize("mode", ["unknown_usage", "local_rejection", "metered_overrun"])
def test_verifier_failure_accounting(mode) -> None:
    from test_api_strong_verifier import _backend, _claim, _evidence, _response

    from scholartrace.model_provider import ProviderInferenceError
    from scholartrace.verification_ablation.runner import _snapshot, _verifier_delta

    dispatched = []

    def handle(request):
        dispatched.append(request)
        if mode == "unknown_usage":
            return httpx.Response(200, json={"model": "gpt-5.6-terra"})
        response = _response()
        payload = response.json()
        payload["usage"]["output_tokens"] = 100000
        return httpx.Response(200, json=payload)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            backend = _backend(client)
            if mode == "local_rejection":
                backend.call_counter.attempted_calls = backend.call_counter.budget.max_calls
            before = _snapshot(backend)
            with pytest.raises(ProviderInferenceError):
                await backend.verify(claim=_claim(), evidence=[_evidence()])
            return _verifier_delta(before, _snapshot(backend))

    metrics = asyncio.run(scenario())
    if mode == "local_rejection":
        assert dispatched == []
        assert metrics["attempted_calls"] == 0
        assert metrics["reference_cost_cny"] == 0
    elif mode == "unknown_usage":
        assert len(dispatched) == 1
        assert metrics["attempted_calls"] == 1
        assert metrics["input_tokens"] is None
        assert metrics["reference_cost_cny"] is None
    else:
        assert len(dispatched) == 1
        assert metrics["output_tokens"] == 100000
        assert metrics["reference_cost_cny"] > 0
