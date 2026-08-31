"""M6 B0-B4 delivery matrix built from existing sanitized stage evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from scholartrace.delivery.models import EvaluationMatrix, EvaluationPhase


def build_m6_evaluation_matrix(*, root: Path) -> EvaluationMatrix:
    phases = [
        EvaluationPhase(
            phase="B0",
            status="validated",
            evidence_report="evaluation/reports/m0_local_model_smoke.json",
            quality_evidence="limited",
            failure_analysis=[
                "Three-case local smoke is compatibility evidence, not capacity evidence."
            ],
        ),
        EvaluationPhase(
            phase="B1",
            status="validated",
            evidence_report="evaluation/reports/m1_local_baseline_smoke.json",
            quality_evidence="limited",
            failure_analysis=[
                "Metadata/abstract baseline cannot establish full-text claim support."
            ],
        ),
        EvaluationPhase(
            phase="B2",
            status="validated",
            evidence_report="evaluation/reports/m2_live_documind_smoke.json",
            quality_evidence="limited",
            failure_analysis=[
                "Three-paper online loop passed; sample size is too small "
                "for general quality claims."
            ],
        ),
        EvaluationPhase(
            phase="B3",
            status="validated",
            evidence_report="evaluation/reports/m6_b3_b4_scored_comparison.json",
            quality_evidence="measured",
            failure_analysis=[
                "The 12-question blind review gave B3 a 4.000000 mean; all eligible "
                "B3 answers scored 4, so the current set has a ceiling effect."
            ],
        ),
        EvaluationPhase(
            phase="B4",
            status="validated",
            evidence_report="evaluation/reports/m6_b3_b4_scored_comparison.json",
            quality_evidence="measured",
            failure_analysis=[
                "B4 had zero eligible quality gain and a 3.916667 overall mean; its "
                "P50/P95 report latency exceeded B3, so default enablement stays blocked."
            ],
        ),
    ]
    blockers = [
        "ScholarGraph default enablement remains blocked: the six eligible questions "
        "showed zero mean quality gain while B4 increased latency.",
    ]
    limitations = [
        "Existing stage reports are sanitized and do not contain raw answers or paper full text.",
        "B4 ScholarGraph context is abstract-only and cannot become full-text Evidence.",
        "All eligible B3 answers scored 4, so this first blind set has a ceiling effect.",
        "Basic capacity evidence is three sequential single-GPU repeats, "
        "not a concurrent load test.",
        "The full paid B3/B4 pair was not repeated after the blind review found no B4 benefit.",
        "Provider billing and isolated GPU-time measurements are not available.",
    ]
    del root  # Paths are intentionally recorded as public evidence references only.
    return EvaluationMatrix(
        generated_at=datetime.now(UTC).isoformat(),
        phases=phases,
        overall_status="delivery_ready_with_notes",
        blocking_reasons=blockers,
        limitations=limitations,
    )
