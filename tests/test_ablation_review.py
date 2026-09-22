from __future__ import annotations

import json
from pathlib import Path

import pytest

from scholartrace.verification_ablation.review import (
    quality_view,
    summarize_review,
    validate_review,
)
from scholartrace.verification_ablation.validation import source_snapshot, validation_status


def test_quality_view_preserves_mixed_and_unrecognized_limitations() -> None:
    prefix = (
        "All prepared claims are marked unverified under the V-off variant; they are "
        "therefore reported as unverified evidence leads rather than confirmed findings."
    )
    report = "\n".join(
        [
            "# Report",
            "  - Status: `unverified`",
            f"- {prefix} The excerpts do not establish a comparable cost profile.",
            "- V-on cannot establish latency because the supplied excerpt has no measurements.",
        ]
    )
    view = quality_view(report)
    assert "Status:" not in view
    assert "The excerpts do not establish a comparable cost profile." in view
    assert "no measurements" in view
    assert prefix not in view


def test_missing_explicit_review_is_rejected() -> None:
    frozen = {"questions": [{"question_id": "q", "split": "formal", "claims": [{"claim_id": "c"}]}]}
    with pytest.raises(ValueError, match="claim_reviews coverage"):
        validate_review({"artifact_sha256": {}}, {"rows": []}, frozen, {"questions": []}, {})


def test_indeterminate_removal_is_not_an_unsupported_interception() -> None:
    review = {
        "finding_reviews": [
            {"question_id": "q", "variant": "V-off", "claim_id": "c", "label": "unsupported"},
            {"question_id": "q", "variant": "V-off", "claim_id": "d", "label": "indeterminate"},
            {"question_id": "q", "variant": "V-on", "claim_id": "e", "label": "supported"},
        ],
        "claim_reviews": [
            {"question_id": "q", "claim_id": c, "label": label}
            for c, label in [("c", "unsupported"), ("d", "indeterminate"), ("e", "supported")]
        ],
        "key_point_reviews": [{"variant": v, "label": "covered"} for v in ("V-on", "V-off")],
    }
    result = summarize_review(review)
    assert result["removed_unsupported_claims"] == 1
    assert result["removed_indeterminate_claims"] == 1
    assert result["by_variant"]["V-off"]["unsupported"] == {"numerator": 1, "denominator": 1}
    assert result["protocol_conclusion"] == "INCONCLUSIVE_CEILING"


def test_validation_does_not_invent_pass_or_accept_stale_snapshot(tmp_path: Path) -> None:
    record = tmp_path / "validation.json"
    assert validation_status(tmp_path, record)["status"] == "unverified"
    source = tmp_path / "README.md"
    source.write_text("version one", encoding="utf-8")
    checks = [
        {"name": n, "exit_code": 0}
        for n in ("pytest", "ruff", "mypy", "compileall", "diff_check", "public_content")
    ]
    record.write_text(
        json.dumps(
            {
                "source_sha256": source_snapshot(tmp_path),
                "checks": checks,
                "status": "passed",
                "source_unchanged_during_checks": True,
            }
        ),
        encoding="utf-8",
    )
    assert validation_status(tmp_path, record)["status"] == "passed"
    data = json.loads(record.read_text())
    data["source_unchanged_during_checks"] = False
    record.write_text(json.dumps(data), encoding="utf-8")
    assert validation_status(tmp_path, record)["status"] == "failed"
    source.write_text("version two", encoding="utf-8")
    assert validation_status(tmp_path, record)["status"] == "stale"
