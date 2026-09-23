from __future__ import annotations

from typing import Any

from scripts.build_sp04_review import (
    reconcile_execution,
    recovery_ledger,
    selection_audit_interpretation,
    status_semantics,
    update_comparison_summary,
)


def test_selection_audit_interpretation_uses_group_statistics() -> None:
    interpretation = selection_audit_interpretation(
        selection_by_question={
            "ordinary-selected": {"selected": 2, "skipped": 0},
            "ordinary-skipped": {"selected": 0, "skipped": 2},
            "difficult-mixed": {"selected": 1, "skipped": 1},
            "difficult-skipped": {"selected": 0, "skipped": 2},
        },
        question_group_by_id={
            "ordinary-selected": "ordinary",
            "ordinary-skipped": "ordinary",
            "difficult-mixed": "difficult",
            "difficult-skipped": "difficult",
        },
        selected_claims_by_group={"ordinary": 2, "difficult": 1},
        skipped_claims_by_group={"ordinary": 2, "difficult": 3},
    )

    assert interpretation == (
        "3 selected Claims (2 ordinary, 1 difficult) occur across 2 "
        "questions (1 ordinary and 1 difficult); 5 skipped Claims "
        "(2 ordinary, 3 difficult) occur across 3 questions "
        "(1 ordinary and 2 difficult). This concentration "
        "limits the candidate's demonstrated coverage of difficult inputs."
    )


def test_recovery_ledger_preserves_unknown_and_collision_history() -> None:
    artifacts: dict[str, dict[str, Any]] = {
        "pre_recovery_snapshot": {
            "unknown_sent": [
                {
                    "id": "attempt:unknown",
                    "question_id": "question-1",
                    "state": "dispatched",
                    "operation": "verifier",
                    "reserved_cost": 0.063,
                }
            ],
            "attempt_states": {"succeeded": 35, "intent": 10},
            "sent_count": 36,
            "known_attempt_cost_cny": 0.29304,
        },
        "full_verification": {
            "attempts": [
                {
                    "attempt_id": "attempt:unknown",
                    "attempted_calls": 1,
                    "actual_reference_cost_cny": 0.008,
                    "previous_attempt_id": None,
                    "state": "succeeded",
                }
            ]
        },
    }

    ledger = recovery_ledger(artifacts)

    assert ledger["identity_collision_count"] == 1
    assert ledger["current_run_new_collisions"] == 0
    record = ledger["historical_unknown_records"][0]
    assert record["historical_state"] == "dispatched"
    assert record["historical_reserved_reference_cost_cny"] == 0.063
    assert record["post_recovery_state"] == "succeeded"
    assert record["identity_reused"] is True
    assert record["successor_attempt_id"] is None
    assert record["parent_link_preserved"] is False
    assert ledger["historical_unknown_count"] == 1
    assert ledger["historical_unknown_count_by_scheme"] == {
        "full_verification": 1,
        "selective_verification": 0,
    }


def test_reconciliation_separates_current_and_historical_unknowns() -> None:
    historical_attempt = {
        "attempt_id": "attempt:historical",
        "attempted_calls": 1,
        "successful_calls": 1,
        "actual_reference_cost_cny": 0.008,
        "state": "succeeded",
    }
    current_full_unknown = {
        "attempt_id": "attempt:current-full",
        "attempted_calls": 1,
        "successful_calls": 0,
        "actual_reference_cost_cny": None,
        "state": "dispatched",
    }
    current_selective_unknown = {
        "attempt_id": "attempt:current-selective",
        "attempted_calls": 1,
        "successful_calls": 0,
        "actual_reference_cost_cny": None,
        "state": "unknown",
    }
    artifacts: dict[str, dict[str, Any]] = {
        "simple_baseline": {
            "usage": {"provider_api_calls": 1, "reference_cost_cny": 0.1}
        },
        "full_verification": {
            "attempts": [historical_attempt, current_full_unknown]
        },
        "selective_verification": {"attempts": [current_selective_unknown]},
        "pre_recovery_snapshot": {
            "unknown_sent": [
                {
                    "id": "attempt:historical",
                    "question_id": "question-1",
                    "state": "dispatched",
                    "operation": "verifier",
                    "reserved_cost": 0.063,
                }
            ],
            "attempt_states": {"succeeded": 35, "intent": 10},
            "sent_count": 36,
            "known_attempt_cost_cny": 0.29304,
        },
    }

    reconciled = reconcile_execution(artifacts)

    assert reconciled["current_attempt_unknown_count"] == 2
    assert reconciled["current_attempt_unknown_count_by_scheme"] == {
        "full_verification": 1,
        "selective_verification": 1,
    }
    assert reconciled["historical_unknown_requests"] == 1


def test_status_semantics_keeps_historical_unknown_out_of_current_count() -> None:
    status = status_semantics(
        rows={},
        guardrails={"candidate_passed": False},
        reconciliation={
            "current_attempt_unknown_count": 0,
            "current_attempt_unknown_count_by_scheme": {
                "full_verification": 0,
                "selective_verification": 0,
            },
            "historical_unknown_requests": 1,
            "historical_replay_duplicate_requests": 35,
        },
        recovery={
            "historical_unknown_count_by_scheme": {
                "full_verification": 1,
                "selective_verification": 0,
            }
        },
    )

    assert status["unknown_count"] == 0
    assert status["current_attempt_unknown_count"] == 0
    assert status["historical_unknown_count"] == 1
    assert status["ledger_resolved_without_unknown"] is False


def test_summary_separates_execution_quality_adoption_and_unknown() -> None:
    summary = {
        "status": "completed",
        "recovery": {
            "full_checkpoint_budget_migrated": True,
            "selective_checkpoint_budget_migrated": False,
        },
        "baseline": {"passed": True},
        "full_verification": {"passed": True},
        "selective_verification": {"passed": True},
    }
    reconciliation = {
        "historical_unknown_requests": 1,
        "historical_replay_duplicate_requests": 35,
    }
    recovery = {
        "historical_unknown_records": [{"attempt_id": "attempt:unknown"}],
        "historical_unknown_count_by_scheme": {
            "full_verification": 1,
            "selective_verification": 0,
        },
    }
    status = {
        "current_run_execution_complete": True,
        "ledger_resolved_without_unknown": False,
        "unknown_count": 0,
        "current_attempt_unknown_count": 0,
        "current_attempt_unknown_count_by_scheme": {
            "full_verification": 0,
            "selective_verification": 0,
        },
        "historical_unknown_count": 1,
        "historical_unknown_count_by_scheme": {
            "full_verification": 1,
            "selective_verification": 0,
        },
        "historical_replay_count": 35,
        "selective_candidate_quality_gate_passed": False,
        "selective_candidate_adopted": False,
    }
    runtime = {
        "fingerprint_sha256": "a" * 64,
        "reasoning_effort": "high",
    }

    updated = update_comparison_summary(
        summary=summary,
        runtime_configuration=runtime,
        reconciliation=reconciliation,
        recovery=recovery,
        status=status,
        logical_resources={"full_verification": {"provider_requests": 50}},
        review_relative_path="evaluation/reports/review.json",
    )

    assert updated["status"] == "execution_completed_with_quality_gate_failure"
    assert updated["legacy_status"] == "completed"
    assert updated["status_semantics"]["current_run_execution_complete"] is True
    assert updated["status_semantics"]["selective_candidate_quality_gate_passed"] is False
    assert updated["status_semantics"]["selective_candidate_adopted"] is False
    assert updated["status_semantics"]["unknown_count"] == 0
    assert updated["status_semantics"]["current_attempt_unknown_count"] == 0
    assert updated["status_semantics"]["historical_unknown_count"] == 1
    assert updated["baseline"]["execution_complete"] is True
    assert updated["full_verification"]["adopted"] is True
    assert updated["full_verification"]["unknown_count"] == 0
    assert updated["full_verification"]["historical_unknown_count"] == 1
    assert updated["selective_verification"]["unknown_count"] == 0
    assert updated["selective_verification"]["historical_unknown_count"] == 0
    assert updated["selective_verification"]["quality_gate_status"] == "failed"
    assert updated["checkpoint_budget_migration"]["full_verification"]["status"] == "applied"
    selective_migration = updated["checkpoint_budget_migration"]["selective_verification"]
    assert selective_migration["required"] is False
    assert selective_migration["applied"] is False
    assert selective_migration["status"] == "not_required"
    assert "no migration was required" in selective_migration["reason"]
    assert updated["recovery_ledger"] == recovery
