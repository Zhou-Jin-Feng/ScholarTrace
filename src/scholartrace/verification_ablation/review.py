"""Lossless review views and explicit, source-bound evaluation inputs."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

DIRECT_STATUS = re.compile(
    r"^\s*-\s*(?:Status|Source Claim status):\s*`(?:unverified|verified_[^`]+)`\s*$",
    re.IGNORECASE,
)
FINDING = re.compile(
    r"^- (?P<text>.+)\n  - Claim: `(?P<claim>[^`]+)`\n"
    r"  - (?:Status|Source Claim status): `(?P<status>[^`]+)`\n"
    r"  - Evidence: (?P<evidence>.+)$",
    re.MULTILINE,
)
# Only exact, pure metadata sentences are removable. Mixed or unfamiliar text is retained.
CONDITION_SENTENCES = frozenset(
    {
        (
            "All findings retain the prepared status unverified because "
            "independent semantic verification was disabled."
        ),
        (
            "All supplied claims retain the prepared unverified status because "
            "independent semantic verification was disabled; no claim is "
            "independently validated here."
        ),
        (
            "All prepared claims are marked unverified in the supplied V-off "
            "packet; this report does not upgrade them to supported findings."
        ),
        (
            "All claims retain the prepared unverified status because independent "
            "semantic verification was disabled under the V-off variant."
        ),
        (
            "Independent semantic verification was disabled in the supplied V-off "
            "variant, so all prepared claim statuses are retained as unverified."
        ),
        (
            "Independent semantic verification was disabled for this V-off "
            "variant; all prepared claim statuses are therefore retained as "
            "unverified."
        ),
        (
            "Independent semantic verification was disabled in the supplied V-off "
            "packet; all findings therefore retain the prepared status unverified."
        ),
        (
            "All supplied claims retain their prepared unverified status; "
            "independent semantic verification was disabled."
        ),
        (
            "Independent semantic verification was disabled in the prepared "
            "packet; all findings therefore retain their prepared unverified "
            "status."
        ),
        (
            "All prepared claims are marked unverified under the V-off variant; "
            "they are therefore reported as unverified evidence leads rather than "
            "confirmed findings."
        ),
    }
)
LABELS = {"supported", "partially_supported", "unsupported", "indeterminate"}
COVERAGE = {"covered", "partially_covered", "missing"}


def quality_view(report: str) -> str:
    output = []
    for line in report.splitlines():
        if DIRECT_STATUS.match(line):
            continue
        if line.startswith("- "):
            body = line[2:]
            for sentence in CONDITION_SENTENCES:
                if body == sentence:
                    body = ""
                    break
                if body.startswith(sentence + " "):
                    body = body[len(sentence) + 1 :]
                    break
            if not body:
                continue
            line = "- " + body
        output.append(line)
    return "\n".join(output).strip() + "\n"


def parse_findings(report: str) -> list[dict[str, Any]]:
    return [
        {
            "text": m.group("text").strip(),
            "claim_id": m.group("claim"),
            "evidence_ids": re.findall(r"`([^`]+)`", m.group("evidence")),
        }
        for m in FINDING.finditer(report)
    ]


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_review(
    review: dict[str, Any],
    archive: dict[str, Any],
    frozen: dict[str, Any],
    reference: dict[str, Any],
    artifact_hashes: dict[str, str],
) -> None:
    if review.get("artifact_sha256") != artifact_hashes:
        raise ValueError("review input artifact identity drifted")
    questions = {q["question_id"]: q for q in frozen["questions"] if q["split"] == "formal"}
    expected_findings = {}
    for row in archive["rows"]:
        q, variant = row["result"]["question_id"], row["result"]["variant"]
        for finding in parse_findings(row["report"]):
            expected_findings[(q, variant, finding["claim_id"])] = finding
    expected_claims = {
        (q, c["claim_id"]) for q, packet in questions.items() for c in packet["claims"]
    }
    expected_points = {
        (q["question_id"], v, p["key_point_id"])
        for q in reference["questions"]
        if q["question_id"] in questions
        for v in ("V-on", "V-off")
        for p in q["key_points"]
    }
    for field, keys, expected, labels in (
        ("finding_reviews", ("question_id", "variant", "claim_id"), set(expected_findings), LABELS),
        ("claim_reviews", ("question_id", "claim_id"), expected_claims, LABELS),
        (
            "key_point_reviews",
            ("question_id", "variant", "key_point_id"),
            expected_points,
            COVERAGE,
        ),
    ):
        entries = review.get(field, [])
        identities = [tuple(e[k] for k in keys) for e in entries]
        if len(identities) != len(set(identities)) or set(identities) != expected:
            raise ValueError(f"{field} coverage is incomplete or duplicated")
        for entry in entries:
            if entry.get("label") not in labels or not entry.get("reason", "").strip():
                raise ValueError(f"{field} contains a missing explicit judgment")
            if not entry.get("source_locator"):
                raise ValueError(f"{field} has no source locator")
    for entry in review["finding_reviews"]:
        key = (entry["question_id"], entry["variant"], entry["claim_id"])
        if entry["finding_sha256"] != text_hash(expected_findings[key]["text"]):
            raise ValueError("reviewed finding wording drifted")


def summarize_review(review: dict[str, Any]) -> dict[str, Any]:
    variants: dict[str, Any] = {}
    for variant in ("V-on", "V-off"):
        labels = Counter(e["label"] for e in review["finding_reviews"] if e["variant"] == variant)
        coverage = Counter(
            e["label"] for e in review["key_point_reviews"] if e["variant"] == variant
        )
        total = sum(labels.values())
        determinate = total - labels["indeterminate"]
        points = sum(coverage.values())
        variants[variant] = {
            "finding_labels": {label: labels[label] for label in sorted(LABELS)},
            "unsupported": {"numerator": labels["unsupported"], "denominator": determinate},
            "indeterminate": labels["indeterminate"],
            "finding_count": total,
            "key_point_coverage": dict(coverage),
            "key_point_count": points,
            "weighted_coverage": (
                (coverage["covered"] + 0.5 * coverage["partially_covered"]) / points
                if points
                else None
            ),
        }
    present = {
        v: {
            (e["question_id"], e["claim_id"])
            for e in review["finding_reviews"]
            if e["variant"] == v
        }
        for v in ("V-on", "V-off")
    }
    claims = {(e["question_id"], e["claim_id"]): e for e in review["claim_reviews"]}
    removed = present["V-off"] - present["V-on"]
    removed_counts = Counter(claims[k]["label"] for k in removed)
    correct = sum(
        e["label"] in {"supported", "partially_supported"} for e in review["claim_reviews"]
    )
    false = removed_counts["supported"] + removed_counts["partially_supported"]
    conclusion = "MIXED_OR_INCONCLUSIVE"
    off, on = variants["V-off"], variants["V-on"]
    reduction = off["unsupported"]["numerator"] - on["unsupported"]["numerator"]
    coverage_known = all(v["weighted_coverage"] is not None for v in variants.values())
    coverage_loss = off["weighted_coverage"] - on["weighted_coverage"] if coverage_known else None
    false_rate = false / correct if correct else None
    if coverage_loss is None or false_rate is None:
        conclusion = "INSUFFICIENT_REVIEW_DATA"
    elif off["unsupported"]["numerator"] < 5:
        conclusion = "INCONCLUSIVE_CEILING"
    elif reduction <= 0 or coverage_loss > 0.10 or (false_rate or 0) > 0.10:
        conclusion = "DO_NOT_ADOPT_AS_IS"
    elif (
        reduction >= 2
        and reduction / off["unsupported"]["numerator"] >= 0.30
        and coverage_loss <= 0.05
        and false_rate is not None
        and false_rate <= 0.10
    ):
        conclusion = "USEFUL_WITH_MEASURED_COST"
    return {
        "by_variant": variants,
        "removed_unsupported_claims": removed_counts["unsupported"],
        "removed_indeterminate_claims": removed_counts["indeterminate"],
        "false_interceptions": {"numerator": false, "denominator": correct},
        "protocol_conclusion": conclusion,
        "interpretation": "Descriptive source-quote review; not independent blind evaluation.",
        "removed_claims": [
            {"question_id": q, "claim_id": c, "label": claims[(q, c)]["label"]}
            for q, c in sorted(removed)
        ],
    }
