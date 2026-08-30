from __future__ import annotations

import asyncio
import json
from pathlib import Path

from scholartrace.verification.fixture_smoke import run_fixture_smoke

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "m4" / "reliability_cases.json"


def test_m4_fixture_is_explicitly_scoped_and_valid_json() -> None:
    fixture = json.loads(FIXTURE.read_text("utf-8"))
    assert fixture["fixture_kind"] == "deterministic_m4_conflict_and_missing_citation"
    assert "not semantic model quality" in fixture["quality_scope"]


def test_m4_zero_cost_fixture_smoke_enforces_critical_claim_gate() -> None:
    summary = asyncio.run(run_fixture_smoke(FIXTURE))
    assert summary["passed"] is True
    assert summary["provider_calls"] == 0
    assert summary["model_provider_calls"] == 0
    assert summary["report_safe"] is True
    assert summary["follow_up_count"] == 1
    assert summary["blocked_critical_claim_ids"] == ["claim:m4:missing-citation"]
