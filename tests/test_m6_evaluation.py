from __future__ import annotations

from pathlib import Path

from scholartrace.delivery.evaluation import build_m6_evaluation_matrix


def test_m6_evaluation_matrix_records_measured_no_benefit() -> None:
    matrix = build_m6_evaluation_matrix(root=Path(__file__).resolve().parents[1])
    assert [item.phase for item in matrix.phases] == ["B0", "B1", "B2", "B3", "B4"]
    assert matrix.overall_status == "delivery_ready_with_notes"
    assert matrix.phases[-1].status == "validated"
    assert matrix.phases[-1].quality_evidence == "measured"
    assert matrix.phases[-1].evidence_report.endswith(
        "m6_b3_b4_scored_comparison.json"
    )
    assert "zero mean quality gain" in matrix.blocking_reasons[0]
