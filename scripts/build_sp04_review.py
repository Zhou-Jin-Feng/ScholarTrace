"""Build the source-bound SP-04 three-scheme review without network calls."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from scholartrace.search.storage import write_json
from scholartrace.verification_ablation.provider_context import COMPACT_CONTEXT_ID
from scholartrace.verification_ablation.review import parse_findings
from scholartrace.verification_ablation.runtime_config import (
    RuntimeConfigurationIdentity,
    runtime_configuration_payload,
)

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_REVIEW = ROOT / "agent/verification-ablation/SP-04-review/source_review.json"
OUTPUT = ROOT / "evaluation/reports/sp_04_three_scheme_review.json"
COMPARISON_SUMMARY = ROOT / "evaluation/reports/sp_04_comparison_summary.json"
RUN_ROOT = (
    ROOT
    / "agent/verification-ablation/SP-04-comparison"
    / "20260922T161810Z"
)
ARTIFACTS = {
    "frozen_inputs": ROOT / "agent/verification-ablation/SP-02/frozen_inputs.json",
    "human_reference": ROOT / "agent/verification-ablation/SP-02/human_reference.json",
    "selection_plan": ROOT / "agent/verification-ablation/SP-03/selection_plan.json",
    "simple_baseline": RUN_ROOT / "simple-baseline/baseline_archive.json",
    "full_verification": RUN_ROOT / "full-verification/formal_archive.json",
    "selective_verification": RUN_ROOT / "selective-verification/formal_archive.json",
    "pre_recovery_snapshot": ROOT
    / "agent/provider-review-20260923/checkpoint-summary.json",
}
SCHEMES = ("simple_baseline", "full_verification", "selective_verification")
CLAIM_LABELS = {"supported", "partially_supported", "unsupported", "indeterminate"}
COVERAGE_LABELS = {"covered", "partially_covered", "missing"}


def selection_audit_interpretation(
    *,
    selection_by_question: Mapping[str, Mapping[str, int]],
    question_group_by_id: Mapping[str, str],
    selected_claims_by_group: Mapping[str, int],
    skipped_claims_by_group: Mapping[str, int],
) -> str:
    selected_questions_by_group: Counter[str] = Counter()
    skipped_questions_by_group: Counter[str] = Counter()
    for question_id, counts in selection_by_question.items():
        group = question_group_by_id[question_id]
        if counts["selected"] > 0:
            selected_questions_by_group[group] += 1
        if counts["skipped"] > 0:
            skipped_questions_by_group[group] += 1

    selected_question_count = sum(selected_questions_by_group.values())
    skipped_question_count = sum(skipped_questions_by_group.values())
    selected_claim_count = sum(selected_claims_by_group.values())
    skipped_claim_count = sum(skipped_claims_by_group.values())
    return (
        f"{selected_claim_count} selected Claims "
        f"({selected_claims_by_group['ordinary']} ordinary, "
        f"{selected_claims_by_group['difficult']} difficult) occur across "
        f"{selected_question_count} questions "
        f"({selected_questions_by_group['ordinary']} ordinary and "
        f"{selected_questions_by_group['difficult']} difficult); "
        f"{skipped_claim_count} skipped Claims "
        f"({skipped_claims_by_group['ordinary']} ordinary, "
        f"{skipped_claims_by_group['difficult']} difficult) occur across "
        f"{skipped_question_count} questions "
        f"({skipped_questions_by_group['ordinary']} ordinary and "
        f"{skipped_questions_by_group['difficult']} difficult). "
        "This concentration limits the candidate's demonstrated coverage "
        "of difficult inputs."
    )


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def question_inputs(frozen: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        wrapper["input"]["question_id"]: wrapper["input"]
        for wrapper in frozen["questions"]
    }


def report_rows(artifacts: Mapping[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    baseline = {
        row["question_id"]: row for row in artifacts["simple_baseline"]["rows"]
    }
    output = {"simple_baseline": baseline}
    for scheme in ("full_verification", "selective_verification"):
        output[scheme] = {
            row["result"]["question_id"]: row
            for row in artifacts[scheme]["rows"]
            if row["result"]["variant"] == "V-on"
        }
    return output


def row_result(scheme: str, row: Mapping[str, Any]) -> Mapping[str, Any]:
    return row if scheme == "simple_baseline" else row["result"]


def disposition_map(row: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["claim_id"]: item for item in row["dispositions"]}


def validate_review(
    review: Mapping[str, Any],
    artifacts: Mapping[str, dict[str, Any]],
    hashes: Mapping[str, str],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    set[str],
]:
    if review.get("artifact_sha256") != hashes:
        raise ValueError("SP-04 review artifact identity drifted")
    inputs = question_inputs(artifacts["frozen_inputs"])
    references = {
        item["question_id"]: item for item in artifacts["human_reference"]["questions"]
    }
    rows = report_rows(artifacts)
    question_ids = set(inputs)
    if len(question_ids) != 8 or set(references) != question_ids:
        raise ValueError("SP-04 question or reference coverage drifted")
    if any(set(scheme_rows) != question_ids for scheme_rows in rows.values()):
        raise ValueError("SP-04 report coverage is incomplete")

    claims = {
        claim["claim_id"]: claim
        for item in inputs.values()
        for claim in item["claims"]
    }
    claim_question = {
        claim["claim_id"]: question_id
        for question_id, item in inputs.items()
        for claim in item["claims"]
    }
    claim_reviews = review.get("claim_reviews")
    if not isinstance(claim_reviews, dict) or set(claim_reviews) != set(claims):
        raise ValueError("SP-04 Claim review coverage is incomplete")
    for claim_id, entry in claim_reviews.items():
        if entry.get("label") not in CLAIM_LABELS:
            raise ValueError(f"{claim_id} has an invalid review label")
        if not entry.get("reason") or not entry.get("source_locator"):
            raise ValueError(f"{claim_id} lacks an explicit source judgment")

    plans = {
        item["question_id"]: item for item in artifacts["selection_plan"]["plans"]
    }
    if set(plans) != question_ids:
        raise ValueError("SP-04 selection-plan coverage drifted")
    skipped = {
        decision["claim_id"]
        for plan in plans.values()
        for decision in plan["decisions"]
        if not decision["selected"]
    }
    if len(skipped) != 37 or not skipped <= set(claim_reviews):
        raise ValueError("SP-04 skipped-Claim audit is incomplete")

    expected_key_points = {
        (question_id, index)
        for question_id, item in references.items()
        for index in range(1, len(item["key_points"]) + 1)
    }
    key_entries = review.get("key_point_reviews")
    if not isinstance(key_entries, list):
        raise ValueError("SP-04 key-point reviews are missing")
    identities = [(item["question_id"], item["key_point_index"]) for item in key_entries]
    if len(identities) != len(set(identities)) or set(identities) != expected_key_points:
        raise ValueError("SP-04 key-point review coverage is incomplete")
    for item in key_entries:
        labels = item.get("labels")
        if not isinstance(labels, dict) or set(labels) != set(SCHEMES):
            raise ValueError("SP-04 key-point scheme coverage is incomplete")
        if any(label not in COVERAGE_LABELS for label in labels.values()):
            raise ValueError("SP-04 key-point review contains an invalid label")
        if not item.get("reason") or not item.get("source_locator"):
            raise ValueError("SP-04 key-point review lacks an explicit judgment")

    findings: dict[str, dict[str, Any]] = {}
    for scheme, scheme_rows in rows.items():
        for question_id, row in scheme_rows.items():
            result = row_result(scheme, row)
            if result["status"] not in {"succeeded", "degraded"}:
                raise ValueError("SP-04 successful report coverage is incomplete")
            parsed = parse_findings(row["report"])
            parsed_ids = [item["claim_id"] for item in parsed]
            included = {
                claim_id
                for claim_id, disposition in disposition_map(row).items()
                if disposition["included"]
            }
            if len(parsed_ids) != len(set(parsed_ids)) or set(parsed_ids) != included:
                raise ValueError(f"{scheme}:{question_id} finding coverage drifted")
            for finding in parsed:
                claim_id = finding["claim_id"]
                if claim_question.get(claim_id) != question_id:
                    raise ValueError("SP-04 report finding has an invalid Claim binding")
                key = f"{scheme}|{question_id}|{claim_id}"
                findings[key] = finding

    overrides = review.get("finding_label_overrides", {})
    if not isinstance(overrides, dict) or not set(overrides) <= set(findings):
        raise ValueError("SP-04 finding override identity is invalid")
    for key, entry in overrides.items():
        if entry.get("label") not in CLAIM_LABELS or not entry.get("reason"):
            raise ValueError(f"{key} has an invalid finding override")

    baseline_dispositions = rows["simple_baseline"]
    full_dispositions = rows["full_verification"]
    selective_dispositions = rows["selective_verification"]
    if any(
        disposition["status"] != "unverified"
        for row in baseline_dispositions.values()
        for disposition in row["dispositions"]
    ):
        raise ValueError("simple baseline state integrity failed")
    for claim_id in skipped:
        question_id = claim_question[claim_id]
        if disposition_map(selective_dispositions[question_id])[claim_id]["status"] != "unverified":
            raise ValueError("selective skipped Claim was upgraded")
    if any(
        disposition["status"] == "unverified"
        for row in full_dispositions.values()
        for disposition in row["dispositions"]
    ):
        raise ValueError("full-verification state coverage is incomplete")

    return inputs, rows, findings, skipped


def finding_labels(
    review: Mapping[str, Any], findings: Mapping[str, dict[str, Any]]
) -> dict[str, str]:
    output: dict[str, str] = {}
    overrides = review.get("finding_label_overrides", {})
    for key, finding in findings.items():
        claim_id = finding["claim_id"]
        output[key] = overrides.get(key, review["claim_reviews"][claim_id])["label"]
    return output


def summarize_quality(
    *,
    review: Mapping[str, Any],
    labels: Mapping[str, str],
    question_ids: set[str],
) -> dict[str, Any]:
    key_entries = [
        item for item in review["key_point_reviews"] if item["question_id"] in question_ids
    ]
    output: dict[str, Any] = {}
    for scheme in SCHEMES:
        finding_counts = Counter(
            label
            for key, label in labels.items()
            if key.split("|", 2)[0] == scheme and key.split("|", 2)[1] in question_ids
        )
        coverage_counts = Counter(item["labels"][scheme] for item in key_entries)
        point_count = sum(coverage_counts.values())
        weighted = (
            coverage_counts["covered"] + 0.5 * coverage_counts["partially_covered"]
        ) / point_count
        output[scheme] = {
            "finding_labels": {
                label: finding_counts[label] for label in sorted(CLAIM_LABELS)
            },
            "finding_count": sum(finding_counts.values()),
            "unsupported": {
                "numerator": finding_counts["unsupported"],
                "denominator": sum(finding_counts.values())
                - finding_counts["indeterminate"],
            },
            "key_point_coverage": {
                label: coverage_counts[label] for label in sorted(COVERAGE_LABELS)
            },
            "key_point_count": point_count,
            "weighted_key_point_coverage": round(weighted, 6),
        }
    return output


def row_resources(scheme: str, row: Mapping[str, Any]) -> dict[str, float | int]:
    result = row_result(scheme, row)
    verifier_attempts = int(result.get("verifier_attempted_calls", 0))
    report_calls = int(result.get("provider_api_calls") or result.get("report_calls") or 0)
    return {
        "provider_requests": report_calls + verifier_attempts,
        "input_tokens": int(result.get("input_tokens") or 0)
        + int(result.get("verifier_input_tokens") or 0),
        "output_tokens": int(result.get("output_tokens") or 0)
        + int(result.get("verifier_output_tokens") or 0),
        "reference_cost_cny": float(result.get("reference_cost_cny") or 0)
        + float(result.get("verifier_reference_cost_cny") or 0),
        "duration_seconds": float(result.get("duration_seconds") or 0)
        + float(result.get("verifier_duration_seconds") or 0),
    }


def aggregate_resources(
    rows: Mapping[str, Mapping[str, Any]], question_ids: Iterable[str]
) -> dict[str, dict[str, float | int]]:
    selected = set(question_ids)
    output: dict[str, dict[str, float | int]] = {}
    for scheme, scheme_rows in rows.items():
        totals: dict[str, float | int] = {
            "provider_requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "reference_cost_cny": 0.0,
            "duration_seconds": 0.0,
        }
        for question_id in selected:
            values = row_resources(scheme, scheme_rows[question_id])
            for key, value in values.items():
                totals[key] += value
        totals["total_tokens"] = int(totals["input_tokens"]) + int(
            totals["output_tokens"]
        )
        totals["reference_cost_cny"] = round(float(totals["reference_cost_cny"]), 7)
        totals["duration_seconds"] = round(float(totals["duration_seconds"]), 6)
        output[scheme] = totals
    return output


HISTORICAL_IDENTITY_LIMITATION = (
    "The historical unknown record was overwritten in place during recovery by a "
    "successful record with the same attempt ID. The immutable pre-recovery snapshot "
    "and final archive preserve the historical unknown and the recovery success, but "
    "the archive has no distinct successor attempt ID or parent link; the missing "
    "relationship cannot be reconstructed retrospectively."
)


def reconcile_execution(
    artifacts: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    baseline_usage = artifacts["simple_baseline"]["usage"]
    full_attempts = artifacts["full_verification"]["attempts"]
    selective_attempts = artifacts["selective_verification"]["attempts"]
    current_attempts = full_attempts + selective_attempts
    current_unknown_count_by_scheme = {
        scheme: sum(
            int(item["attempted_calls"])
            for item in attempts
            if item["state"] in {"dispatched", "unknown"}
        )
        for scheme, attempts in (
            ("full_verification", full_attempts),
            ("selective_verification", selective_attempts),
        )
    }
    current_unknown_count = sum(current_unknown_count_by_scheme.values())
    snapshot = artifacts["pre_recovery_snapshot"]
    historical_unknown = snapshot["unknown_sent"]
    current_by_id = {item["attempt_id"]: item for item in full_attempts}
    collisions = [
        item for item in historical_unknown if item["id"] in current_by_id
    ]
    if len(historical_unknown) != 1 or len(collisions) != 1:
        raise ValueError("SP-04 historical unknown reconciliation drifted")
    if current_by_id[collisions[0]["id"]]["state"] != "succeeded":
        raise ValueError("SP-04 recovered request result is missing")
    current_sent = sum(int(item["attempted_calls"]) for item in current_attempts)
    current_success = sum(int(item["successful_calls"]) for item in current_attempts)
    baseline_sent = int(baseline_usage["provider_api_calls"])
    baseline_success = int(baseline_usage["provider_api_calls"])
    historical_known = int(snapshot["attempt_states"]["succeeded"])
    historical_sent = int(snapshot["sent_count"])
    if historical_sent != historical_known + len(historical_unknown):
        raise ValueError("SP-04 historical sent/success reconciliation drifted")
    current_known_cost = float(baseline_usage["reference_cost_cny"]) + sum(
        float(item["actual_reference_cost_cny"] or 0) for item in current_attempts
    )
    historical_known_cost = float(snapshot["known_attempt_cost_cny"])
    known_cost = current_known_cost + historical_known_cost
    unknown_reserve = sum(float(item["reserved_cost"]) for item in historical_unknown)
    return {
        "basis": "pre-recovery snapshot plus post-recovery archives",
        "historical_successful_provider_requests": historical_known,
        "post_recovery_successful_provider_requests": baseline_success + current_success,
        "successful_provider_requests": baseline_success
        + current_success
        + historical_known,
        "current_attempt_unknown_count": current_unknown_count,
        "current_attempt_unknown_count_by_scheme": current_unknown_count_by_scheme,
        "historical_unknown_requests": len(historical_unknown),
        "historical_replay_duplicate_requests": historical_known,
        "total_attempted_provider_requests": baseline_sent + current_sent + historical_sent,
        "post_recovery_known_reference_cost_cny": round(current_known_cost, 7),
        "historical_known_reference_cost_cny": round(historical_known_cost, 7),
        "known_reference_cost_cny": round(known_cost, 7),
        "unknown_reserved_reference_cost_cny": round(unknown_reserve, 6),
        "known_plus_unknown_reserve_cny": round(known_cost + unknown_reserve, 7),
        "provider_actual_billing": "unknown",
        "recovery_identity_collision_count": len(collisions),
        "reconciliation_note": (
            "The recovery wrapper omitted existing attempts before this run, so the "
            "recovery re-issued all full-verification requests, reused historical attempt IDs "
            "in the post-recovery archive, and overwrote the earlier success records. The "
            "immutable snapshot preserves 35 historical successes, one unknown request, and "
            "their known or reserved reference amounts. Those amounts and the 35 replayed "
            "requests are counted separately here; the wrapper is now covered by regression "
            "tests."
        ),
        "excluded_scope": (
            "Provider diagnostics and max-configuration probes outside the fixed SP-04 run "
            "are recorded separately and are not included in this run total."
        ),
    }


def runtime_configuration_identity(
    artifacts: Mapping[str, dict[str, Any]],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Recover the runtime identity from the frozen manifest and run summary."""

    manifest = artifacts["full_verification"]["manifest"]
    existing = summary.get("runtime_configuration")
    if isinstance(existing, dict):
        return dict(existing)
    limits = summary.get("request_limits", {})
    report_max = int(limits.get("report_max_output_tokens", 4_000))
    verifier_max = int(limits.get("verifier_max_output_tokens", 2_000))
    compact_context = bool(summary["compact_context"])
    identity = RuntimeConfigurationIdentity.model_validate(
        {
            "model_profile": str(manifest["model_profile"]),
            "model_identifier": str(summary["model"]),
            "provider_protocol": str(manifest["provider_protocol"]),
            "provider_id": str(summary["provider_id"]),
            "provider_hostname": str(summary["provider_hostname"]),
            "structured_output_mode": manifest.get("structured_output_mode"),
            "reasoning_effort": str(summary["reasoning_effort"]),
            "streaming": bool(summary["stream_responses"]),
            "compact_context": compact_context,
            "context_transform_id": (
                COMPACT_CONTEXT_ID if compact_context else "none"
            ),
            "report_max_output_tokens": report_max,
            "verifier_max_output_tokens": verifier_max,
            "request_timeout_seconds": float(manifest["request_timeout_seconds"]),
            "max_response_bytes": int(manifest["max_response_bytes"]),
        }
    )
    payload = runtime_configuration_payload(identity)
    payload["identity_source"] = "frozen_manifest_plus_historical_entrypoint"
    return payload


def recovery_ledger(artifacts: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    """Expose unknown and identity-collision history at request granularity."""

    snapshot = artifacts["pre_recovery_snapshot"]
    full_attempts = artifacts["full_verification"]["attempts"]
    by_id = {item["attempt_id"]: item for item in full_attempts}
    unknown_records = []
    for item in snapshot["unknown_sent"]:
        attempt_id = str(item["id"])
        current = by_id.get(attempt_id)
        if current is None:
            raise ValueError(f"post-recovery archive is missing {attempt_id}")
        unknown_records.append(
            {
                "attempt_id": attempt_id,
                "question_id": str(item["question_id"]),
                "historical_scheme": "full_verification",
                "operation": str(item["operation"]),
                "historical_state": str(item["state"]),
                "historical_attempted_calls": 1,
                "historical_reserved_reference_cost_cny": float(item["reserved_cost"]),
                "historical_snapshot_source": (
                    "agent/provider-review-20260923/checkpoint-summary.json"
                ),
                "post_recovery_state": str(current["state"]),
                "post_recovery_attempted_calls": int(current["attempted_calls"]),
                "post_recovery_actual_reference_cost_cny": current.get(
                    "actual_reference_cost_cny"
                ),
                "identity_reused": True,
                "successor_attempt_id": None,
                "parent_link_preserved": bool(current.get("previous_attempt_id")),
                "historical_success_records_available": False,
                "limitation": HISTORICAL_IDENTITY_LIMITATION,
            }
        )
    return {
        "historical_success_aggregate_count": int(
            snapshot["attempt_states"]["succeeded"]
        ),
        "historical_success_records_available": False,
        "historical_unknown_count": len(unknown_records),
        "historical_unknown_count_by_scheme": {
            "full_verification": len(unknown_records),
            "selective_verification": 0,
        },
        "historical_unknown_records": unknown_records,
        "historical_intent_count": int(snapshot["attempt_states"]["intent"]),
        "historical_sent_count": int(snapshot["sent_count"]),
        "historical_known_reference_cost_cny": float(snapshot["known_attempt_cost_cny"]),
        "identity_collision_count": len(unknown_records),
        "current_run_new_collisions": 0,
        "baseline_request_journal": {
            "available": False,
            "scope": "aggregate_archive_only",
            "reason": (
                "The simple baseline archive stores one report request per question and "
                "aggregate usage, but it does not store request-level intent/dispatch/result "
                "records. Recovery and cost summaries must not claim baseline request-level "
                "traceability."
            ),
        },
    }


def status_semantics(
    *,
    rows: Mapping[str, Mapping[str, Mapping[str, Any]]],
    guardrails: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    recovery: Mapping[str, Any],
) -> dict[str, Any]:
    """Separate execution, quality-gate, adoption, and unknown-ledger outcomes."""

    scheme_rows_complete = all(
        row_result(scheme, row)["status"] in {"succeeded", "degraded"}
        for scheme, scheme_rows in rows.items()
        for row in scheme_rows.values()
    )
    current_unknown_count = int(reconciliation["current_attempt_unknown_count"])
    historical_unknown_count = int(reconciliation["historical_unknown_requests"])
    return {
        "current_run_execution_complete": scheme_rows_complete,
        "ledger_resolved_without_unknown": (
            current_unknown_count == 0 and historical_unknown_count == 0
        ),
        "unknown_count": current_unknown_count,
        "current_attempt_unknown_count": current_unknown_count,
        "current_attempt_unknown_count_by_scheme": dict(
            reconciliation["current_attempt_unknown_count_by_scheme"]
        ),
        "historical_unknown_count": historical_unknown_count,
        "historical_unknown_count_by_scheme": dict(
            recovery["historical_unknown_count_by_scheme"]
        ),
        "historical_replay_count": int(
            reconciliation["historical_replay_duplicate_requests"]
        ),
        "selective_candidate_quality_gate_passed": bool(guardrails["candidate_passed"]),
        "selective_candidate_adopted": False,
        "formal_path": "full_verification",
        "formal_path_changed": False,
        "interpretation": (
            "unknown_count is the unresolved count in current attempt records. Historical "
            "unknown requests are reported separately and remain part of the unresolved "
            "ledger history. Execution completion, quality-gate outcome, adoption, and "
            "unknown-ledger state must not be collapsed into a single passed flag."
        ),
    }


def update_comparison_summary(
    *,
    summary: Mapping[str, Any],
    runtime_configuration: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    recovery: Mapping[str, Any],
    status: Mapping[str, Any],
    logical_resources: Mapping[str, Any],
    review_relative_path: str,
) -> dict[str, Any]:
    """Add explicit status/resource semantics to the public run summary."""

    updated = dict(summary)
    fingerprint = runtime_configuration.get("fingerprint_sha256")
    if not isinstance(fingerprint, str):
        raise ValueError("runtime configuration fingerprint is missing")
    legacy_status = summary.get("legacy_status") or summary.get("status")
    if legacy_status == "execution_completed_with_quality_gate_failure":
        legacy_status = "completed"
    updated["legacy_status"] = legacy_status
    updated["status"] = "execution_completed_with_quality_gate_failure"
    updated["runtime_configuration"] = dict(runtime_configuration)
    updated["runtime_configuration_sha256"] = fingerprint
    updated["status_semantics"] = dict(status)
    updated["resource_scopes"] = {
        "logical_scheme_resources": dict(logical_resources),
        "actual_execution_ledger": dict(reconciliation),
        "basis": (
            "Logical scheme resources describe the compared schemes; the actual execution "
            "ledger includes historical replay, unknown reserve, and baseline sharing."
        ),
    }
    updated["recovery_ledger"] = dict(recovery)
    recovery_metadata = summary.get("recovery")
    if not isinstance(recovery_metadata, dict):
        raise ValueError("comparison summary is missing recovery metadata")
    budget_migration: dict[str, dict[str, Any]] = {}
    for scheme, field in (
        ("full_verification", "full_checkpoint_budget_migrated"),
        ("selective_verification", "selective_checkpoint_budget_migrated"),
    ):
        if field not in recovery_metadata:
            raise ValueError(f"comparison summary is missing {field}")
        migrated = bool(recovery_metadata[field])
        budget_migration[scheme] = {
            "required": migrated,
            "applied": migrated,
            "status": "applied" if migrated else "not_required",
            "reason": (
                "The checkpoint budget policy differed from the current manifest and "
                "was migrated after execution identity validation."
                if migrated
                else "The checkpoint manifest already matched the current budget policy; "
                "no migration was required or performed."
            ),
        }
    updated["checkpoint_budget_migration"] = budget_migration
    updated["baseline_request_journal"] = {
        "available": False,
        "scope": "aggregate_archive_only",
    }
    updated["review_output"] = review_relative_path
    updated["limitations"] = [
        "One fixed run over eight questions; repeated-generation variance was not measured.",
        "The source review is assistant-assisted and is not an independent human review.",
        "Costs are local reference estimates; Provider actual billing is unavailable.",
        "The formal run used reasoning=high; max evidence remains separate.",
        "The baseline has aggregate usage but no request-level attempt journal.",
        HISTORICAL_IDENTITY_LIMITATION,
    ]
    baseline_entry = updated.get("baseline")
    if not isinstance(baseline_entry, dict):
        raise ValueError("comparison summary is missing baseline")
    baseline_entry["execution_complete"] = bool(baseline_entry.get("passed"))
    baseline_entry["quality_gate_status"] = "not_applicable_within_run"
    baseline_entry["adopted"] = False
    baseline_entry["unknown_count"] = 0
    for scheme in ("full_verification", "selective_verification"):
        entry = updated.get(scheme)
        if not isinstance(entry, dict):
            raise ValueError(f"comparison summary is missing {scheme}")
        entry["execution_complete"] = bool(entry.get("passed"))
        if scheme == "full_verification":
            entry["quality_gate_status"] = "not_applicable_within_run"
            entry["adopted"] = True
        else:
            entry["quality_gate_status"] = (
                "passed" if status["selective_candidate_quality_gate_passed"] else "failed"
            )
            entry["adopted"] = bool(status["selective_candidate_adopted"])
        entry["current_attempt_unknown_count"] = int(
            status["current_attempt_unknown_count_by_scheme"][scheme]
        )
        entry["unknown_count"] = entry["current_attempt_unknown_count"]
        entry["historical_unknown_count"] = int(
            status["historical_unknown_count_by_scheme"][scheme]
        )
    return updated


def main() -> int:
    artifacts = {name: load_json(path) for name, path in ARTIFACTS.items()}
    hashes = {name: digest(path) for name, path in ARTIFACTS.items()}
    summary = load_json(COMPARISON_SUMMARY)
    review = load_json(PRIVATE_REVIEW)
    inputs, rows, findings, skipped = validate_review(review, artifacts, hashes)
    plans = {
        item["question_id"]: item for item in artifacts["selection_plan"]["plans"]
    }
    labels = finding_labels(review, findings)
    ordinary = {
        question_id
        for question_id, item in inputs.items()
        if "cross_paper_comparison" not in item["categories"]
    }
    difficult = set(inputs) - ordinary
    group_by_question = {
        question_id: "difficult" if question_id in difficult else "ordinary"
        for question_id in inputs
    }
    if len(ordinary) != 4 or len(difficult) != 4:
        raise ValueError("SP-04 ordinary/difficult split drifted")

    quality = summarize_quality(
        review=review,
        labels=labels,
        question_ids=set(inputs),
    )
    quality_by_group = {
        "ordinary": summarize_quality(
            review=review,
            labels=labels,
            question_ids=ordinary,
        ),
        "difficult": summarize_quality(
            review=review,
            labels=labels,
            question_ids=difficult,
        ),
    }
    baseline_findings = {
        tuple(key.split("|", 2)[1:])
        for key in findings
        if key.startswith("simple_baseline|")
    }
    removals: dict[str, Any] = {}
    correct_claim_count = sum(
        entry["label"] in {"supported", "partially_supported"}
        for entry in review["claim_reviews"].values()
    )
    for scheme in ("full_verification", "selective_verification"):
        present = {
            tuple(key.split("|", 2)[1:])
            for key in findings
            if key.startswith(f"{scheme}|")
        }
        removed = sorted(baseline_findings - present)
        removed_labels = Counter(
            review["claim_reviews"][claim_id]["label"] for _, claim_id in removed
        )
        false_interceptions = sum(
            removed_labels[label] for label in ("supported", "partially_supported")
        )
        removals[scheme] = {
            "removed_finding_count": len(removed),
            "removed_labels": {
                label: removed_labels[label] for label in sorted(CLAIM_LABELS)
            },
            "false_interceptions": {
                "numerator": false_interceptions,
                "denominator": correct_claim_count,
            },
            "removed_claims": [
                {
                    "question_id": question_id,
                    "claim_id": claim_id,
                    "label": review["claim_reviews"][claim_id]["label"],
                }
                for question_id, claim_id in removed
            ],
        }

    claim_question = {
        claim["claim_id"]: question_id
        for question_id, item in inputs.items()
        for claim in item["claims"]
    }
    selected = set(review["claim_reviews"]) - skipped
    selected_counts = Counter(review["claim_reviews"][item]["label"] for item in selected)
    skipped_counts = Counter(review["claim_reviews"][item]["label"] for item in skipped)
    selection_by_question = {
        question_id: {
            "selected": len(plans[question_id]["selected_claim_ids"]),
            "skipped": len(plans[question_id]["skipped_claim_ids"]),
        }
        for question_id in sorted(plans)
    }
    selection_categories: dict[str, Counter[str]] = {
        "selected": Counter(),
        "skipped": Counter(),
    }
    for question_id, plan in plans.items():
        category = group_by_question[question_id]
        for decision in plan["decisions"]:
            outcome = "selected" if decision["selected"] else "skipped"
            selection_categories[outcome][category] += 1
    full_coverage = quality["full_verification"]["weighted_key_point_coverage"]
    selective_coverage = quality["selective_verification"][
        "weighted_key_point_coverage"
    ]
    unsupported_delta = (
        quality["selective_verification"]["unsupported"]["numerator"]
        - quality["full_verification"]["unsupported"]["numerator"]
    )
    coverage_drop = round(full_coverage - selective_coverage, 6)
    state_integrity = all(
        disposition_map(rows["selective_verification"][claim_question[claim_id]])[
            claim_id
        ]["status"]
        == "unverified"
        for claim_id in skipped
    )
    guardrails = {
        "coverage_drop": coverage_drop,
        "coverage_drop_limit": 0.05,
        "coverage_guardrail_passed": coverage_drop <= 0.05,
        "unsupported_finding_delta": unsupported_delta,
        "unsupported_delta_limit": 1,
        "unsupported_guardrail_passed": unsupported_delta <= 1,
        "skipped_claim_state_integrity": state_integrity,
    }
    guardrails["candidate_passed"] = all(
        (
            guardrails["coverage_guardrail_passed"],
            guardrails["unsupported_guardrail_passed"],
            state_integrity,
        )
    )

    resources = aggregate_resources(rows, inputs)
    execution_reconciliation = reconcile_execution(artifacts)
    recovery = recovery_ledger(artifacts)
    runtime_configuration = runtime_configuration_identity(artifacts, summary)
    execution_status = status_semantics(
        rows=rows,
        guardrails=guardrails,
        reconciliation=execution_reconciliation,
        recovery=recovery,
    )
    baseline_cost = float(resources["simple_baseline"]["reference_cost_cny"])
    full_incremental_cost = float(resources["full_verification"]["reference_cost_cny"])
    selective_incremental_cost = float(
        resources["selective_verification"]["reference_cost_cny"]
    )
    standalone_scheme_costs = {
        "simple_baseline": round(baseline_cost, 7),
        "full_verification": round(baseline_cost + full_incremental_cost, 7),
        "selective_verification": round(baseline_cost + selective_incremental_cost, 7),
    }

    payload = {
        "schema_version": "1.0",
        "purpose": "scholartrace-sp04-three-scheme-source-review",
        "status": "verified_with_limitations",
        "run_id": "20260922T161810Z",
        "runtime_configuration": runtime_configuration,
        "runtime_configuration_sha256": runtime_configuration["fingerprint_sha256"],
        "status_semantics": execution_status,
        "configuration": runtime_configuration,
        "artifact_sha256": hashes,
        "review_method": {
            "mode": review["reviewer_mode"],
            "blinded": review["blinded"],
            "reviewed_at": review["reviewed_at"],
            "reviewer_identity": review["reviewer_identity"],
            "scope": review["scope"],
        },
        "question_groups": {
            "ordinary": sorted(ordinary),
            "difficult": sorted(difficult),
            "rule": (
                "Questions tagged cross_paper_comparison are difficult; "
                "the others are ordinary."
            ),
        },
        "audit_coverage": {
            "questions": len(inputs),
            "claims": len(review["claim_reviews"]),
            "skipped_claims_reviewed": len(skipped),
            "generated_reports": sum(len(scheme_rows) for scheme_rows in rows.values()),
            "key_points_per_scheme": len(review["key_point_reviews"]),
        },
        "claim_audit": {
            "all_claim_labels": dict(
                sorted(
                    Counter(
                        item["label"] for item in review["claim_reviews"].values()
                    ).items()
                )
            ),
            "selected_claim_labels": {
                label: selected_counts[label] for label in sorted(CLAIM_LABELS)
            },
            "skipped_claim_labels": {
                label: skipped_counts[label] for label in sorted(CLAIM_LABELS)
            },
            "unsupported_selected": selected_counts["unsupported"],
            "unsupported_skipped": skipped_counts["unsupported"],
        },
        "selection_audit": {
            "selected_by_question": selection_by_question,
            "selected_claim_count_by_question_group": {
                group: selection_categories["selected"][group]
                for group in ("ordinary", "difficult")
            },
            "skipped_claim_count_by_question_group": {
                group: selection_categories["skipped"][group]
                for group in ("ordinary", "difficult")
            },
            "interpretation": selection_audit_interpretation(
                selection_by_question=selection_by_question,
                question_group_by_id=group_by_question,
                selected_claims_by_group=selection_categories["selected"],
                skipped_claims_by_group=selection_categories["skipped"],
            ),
        },
        "quality": quality,
        "quality_by_group": quality_by_group,
        "removal_audit": removals,
        "logical_resources": resources,
        "resource_accounting": {
            "shared_baseline_cost_cny": round(baseline_cost, 7),
            "condition_specific_incremental_cost_cny": {
                "full_verification": round(full_incremental_cost, 7),
                "selective_verification": round(selective_incremental_cost, 7),
            },
            "standalone_scheme_cost_cny": standalone_scheme_costs,
            "basis": (
                "logical_resources for the two verification schemes contain only the V-on "
                "condition; their shared V-off report rows are represented once as the "
                "simple baseline. Standalone costs add that baseline to each scheme."
            ),
        },
        "logical_resources_by_group": {
            "ordinary": aggregate_resources(rows, ordinary),
            "difficult": aggregate_resources(rows, difficult),
        },
        "actual_execution_reconciliation": execution_reconciliation,
        "recovery_ledger": recovery,
        "resource_scopes": {
            "logical_scheme_resources": resources,
            "actual_execution_ledger": execution_reconciliation,
            "basis": (
                "Logical scheme resources describe the compared schemes; the actual "
                "execution ledger includes historical replay, unknown reserve, and "
                "baseline sharing."
            ),
        },
        "candidate_guardrails": guardrails,
        "decision": {
            "selective_candidate": "experimental_only",
            "formal_path": "retain_full_verification",
            "reason": (
                "The selective candidate preserved weighted key-point coverage and state "
                "integrity but left two unsupported bibliography-derived findings in the "
                "reports, exceeding the frozen +1 guardrail."
            ),
            "simple_baseline_boundary": (
                "Suitable for direct descriptive synthesis when every Claim remains visibly "
                "unverified and the user accepts source review; it is not a replacement for "
                "strong verification on evidence-incomplete or adversarial packets."
            ),
            "complex_path_boundary": (
                "Useful when weak Evidence bindings can introduce unsupported findings, but it "
                "adds substantial calls and can remove a correct packet-level absence statement."
            ),
        },
        "limitations": [
            (
                "This is one fixed run over eight questions; no repeated-generation "
                "variance was measured."
            ),
            (
                "The review is an author source review and was not blinded or "
                "independently replicated."
            ),
            "Judgments are limited to supplied excerpts rather than full-paper review.",
            (
                "The formal run used reasoning=high; max probe failures and unknown "
                "stream termination are separate compatibility evidence."
            ),
            "Costs are local reference estimates; Provider actual billing is unavailable.",
            "The baseline has aggregate successful usage but no request-level attempt journal.",
            HISTORICAL_IDENTITY_LIMITATION,
        ],
    }
    write_json(OUTPUT, payload)
    updated_summary = update_comparison_summary(
        summary=summary,
        runtime_configuration=runtime_configuration,
        reconciliation=execution_reconciliation,
        recovery=recovery,
        status=execution_status,
        logical_resources=resources,
        review_relative_path=OUTPUT.relative_to(ROOT).as_posix(),
    )
    write_json(COMPARISON_SUMMARY, updated_summary)
    print(
        json.dumps(
            {
                "status": payload["status"],
                "candidate_passed": guardrails["candidate_passed"],
                "output": OUTPUT.relative_to(ROOT).as_posix(),
                "summary": COMPARISON_SUMMARY.relative_to(ROOT).as_posix(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
