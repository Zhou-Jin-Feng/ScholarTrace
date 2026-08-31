from pathlib import Path

import pytest

from scholartrace.scholargraph.pilot_input import load_m2_pilot_artifacts

ROOT = Path(__file__).resolve().parents[1]


def test_real_m2_artifact_forms_a_deterministically_valid_pilot_packet() -> None:
    report = ROOT / "artifacts" / "m2-evidence-live" / "evidence_report.json"
    if not report.is_file():
        pytest.skip("requires the private local M2 live Evidence artifact")
    packet = load_m2_pilot_artifacts(
        report_path=report,
        paper_fixture_path=ROOT / "tests" / "fixtures" / "documind" / "m2_three_papers.json",
    )
    assert packet.validation.outcome == "succeeded"
    assert len(packet.claims) == 12
    assert len(packet.evidence) == 11
    assert len(packet.papers) == 3
    assert len(packet.bindings) == 3
    assert len(packet.paper_pool_sha256) == 64
    assert len(packet.source_report_sha256) == 64
