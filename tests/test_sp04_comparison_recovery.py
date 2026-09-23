from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sa04_synthetic import make_synthetic_inputs
from test_sa05_formal_checkpoint import _manifest

from scholartrace.verification_ablation import (
    DeterministicFixtureAblationReportGenerator,
    DeterministicFixtureAblationVerifier,
    VerificationAblationRunner,
)
from scripts import run_sp04_comparison


def test_scheme_resume_forwards_existing_attempt_history(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    inputs = make_synthetic_inputs()
    manifest = _manifest(inputs)
    prior = asyncio.run(
        VerificationAblationRunner(
            report_generator=DeterministicFixtureAblationReportGenerator(),
            verifier_backend=DeterministicFixtureAblationVerifier(),
            allow_fixture=True,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:sp04-resume-forwarding",
        )
    ).attempts[:1]
    captured: dict[str, Any] = {}

    class CapturingRunner:
        def __init__(self, **_: Any) -> None:
            pass

        async def run(self, **kwargs: Any) -> SimpleNamespace:
            captured.update(kwargs)
            return SimpleNamespace(rows=[], result_usage_by_variant={})

    monkeypatch.setattr(run_sp04_comparison, "ROOT", tmp_path)
    monkeypatch.setattr(run_sp04_comparison, "enabled_policy", lambda _: object())
    monkeypatch.setattr(run_sp04_comparison, "VerificationAblationRunner", CapturingRunner)
    monkeypatch.setattr(
        run_sp04_comparison,
        "OpenAICompatibleSemanticVerifier",
        lambda **_: object(),
    )
    monkeypatch.setattr(
        run_sp04_comparison,
        "OpenAICompatibleAblationReportGenerator",
        lambda **_: object(),
    )
    monkeypatch.setattr(run_sp04_comparison, "write_artifacts", lambda **_: None)

    result = asyncio.run(
        run_sp04_comparison.run_ablation_scheme(
            name="full-verification",
            manifest=manifest,
            inputs=inputs,
            settings=object(),
            client=object(),
            existing_rows=[],
            existing_attempts=prior,
            selector=None,
            run_id="run:sp04-resume-forwarding",
            private_dir=tmp_path / "private",
            public_path=tmp_path / "public.json",
            allocated_cost_cny=1.0,
            reasoning_effort="high",
            compact_context=False,
            retry_unknown=True,
            streaming=False,
        )
    )

    assert result["checkpoint"] == "private/formal_checkpoint.json"
    assert result["public_output"] == "public.json"
    assert captured["existing_attempts"] == prior


def test_explicit_unknown_retry_appends_linked_attempt() -> None:
    inputs = make_synthetic_inputs()
    source_manifest = _manifest(inputs)
    manifest = source_manifest.model_copy(
        update={
            "budget": source_manifest.budget.model_copy(
                update={
                    "max_reference_cost_cny": 1.0,
                    "unknown_usage_policy": "bounded_reserve",
                    "unknown_attempt_reserve_cny": 0.01,
                }
            )
        }
    )
    initial = asyncio.run(
        VerificationAblationRunner(
            report_generator=DeterministicFixtureAblationReportGenerator(),
            verifier_backend=DeterministicFixtureAblationVerifier(),
            allow_fixture=True,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:sp04-linked-retry",
        )
    )
    first = next(item for item in initial.attempts if item.operation == "verifier")
    unknown = first.model_copy(
        update={
            "state": "dispatched",
            "completed_at": None,
            "successful_calls": 0,
            "input_tokens": None,
            "output_tokens": None,
            "actual_reference_cost_cny": None,
            "duration_seconds": None,
            "verification_result": None,
        }
    )

    resumed = asyncio.run(
        VerificationAblationRunner(
            report_generator=DeterministicFixtureAblationReportGenerator(),
            verifier_backend=DeterministicFixtureAblationVerifier(),
            allow_fixture=True,
        ).run(
            manifest=manifest,
            inputs=inputs,
            run_id="run:sp04-linked-retry",
            existing_attempts=[unknown],
            retry_unknown=True,
        )
    )
    chain = sorted(
        (
            item
            for item in resumed.attempts
            if item.operation == "verifier" and item.subject_id == unknown.subject_id
        ),
        key=lambda item: item.attempt_number,
    )

    assert [item.state for item in chain] == ["dispatched", "succeeded"]
    assert chain[1].attempt_number == 2
    assert chain[1].previous_attempt_id == chain[0].attempt_id


def test_preview_result_is_written_to_public_recovery_report(
    monkeypatch: Any,
) -> None:
    preview: dict[str, Any] = {
        "status": "preview_only",
        "network_requests_sent": 0,
    }
    written: list[tuple[Path, dict[str, Any]]] = []
    monkeypatch.setattr(sys, "argv", ["run_sp04_comparison.py", "--preview"])

    async def fake_run(_: Any) -> dict[str, Any]:
        return preview

    monkeypatch.setattr(run_sp04_comparison, "run", fake_run)
    monkeypatch.setattr(
        run_sp04_comparison,
        "write_json",
        lambda path, payload: written.append((path, dict(payload))),
    )

    exit_code = run_sp04_comparison.main()

    expected_path = run_sp04_comparison.PUBLIC_ROOT / "sp_04_recovery_preview.json"
    assert exit_code == 0
    assert len(written) == 1
    assert written[0][0] == expected_path
    assert written[0][1]["output"] == "evaluation/reports/sp_04_recovery_preview.json"
    assert written[0][1]["network_requests_sent"] == 0
