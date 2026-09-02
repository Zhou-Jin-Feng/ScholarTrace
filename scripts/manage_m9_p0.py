"""Capture, review, and export the private M9-P0 real-miss observation ledger."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from scholartrace.scholargraph.real_miss import (
    M9CaptureSubmission,
    M9ObservationError,
    M9P0Store,
    M9ReviewSubmission,
    require_project_agent_path,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "agent" / "m9-p0" / "observations.sqlite"
DEFAULT_PUBLIC_REPORT = ROOT / "evaluation" / "reports" / "m9_p0_status.json"


def _private_json(path: Path) -> dict[str, object]:
    source = require_project_agent_path(path, project_root=ROOT)
    payload = json.loads(source.read_text("utf-8"))
    if not isinstance(payload, dict):
        raise M9ObservationError("private M9 input must be a JSON object")
    return payload


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture", help="Append one consecutive observation")
    capture.add_argument("--input", type=Path, required=True)
    capture.add_argument("--b5-snapshot", type=Path)
    capture.add_argument("--public-report", type=Path, default=DEFAULT_PUBLIC_REPORT)

    review = subparsers.add_parser("review", help="Append one human-review revision")
    review.add_argument("--input", type=Path, required=True)
    review.add_argument("--public-report", type=Path, default=DEFAULT_PUBLIC_REPORT)

    status = subparsers.add_parser("status", help="Print a sanitized gate summary")
    status.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    store: M9P0Store | None = None
    output: dict[str, object]
    try:
        store = M9P0Store(args.database, project_root=ROOT)
        if args.command == "capture":
            capture_payload = _private_json(args.input)
            if args.b5_snapshot is not None:
                if "b5_snapshot" in capture_payload:
                    raise M9ObservationError(
                        "provide B5 snapshot inline or by --b5-snapshot, not both"
                    )
                capture_payload["b5_snapshot"] = _private_json(args.b5_snapshot)
            capture_submission = M9CaptureSubmission.model_validate(capture_payload)
            observation = store.capture(capture_submission)
            report_sha256 = store.write_public_report(args.public_report)
            output = {
                "record_id": observation.record_id,
                "sequence_id": observation.sequence_id,
                "question_sha256": observation.question_sha256,
                "public_report_sha256": report_sha256,
                "decision": store.public_report().decision.value,
            }
        elif args.command == "review":
            review_submission = M9ReviewSubmission.model_validate(
                _private_json(args.input)
            )
            review = store.review(review_submission)
            report_sha256 = store.write_public_report(args.public_report)
            output = {
                "record_id": review_submission.record_id,
                "review_revision": review.revision,
                "review_entry_sha256": review.review_entry_sha256,
                "public_report_sha256": report_sha256,
                "decision": store.public_report().decision.value,
            }
        else:
            report = store.public_report()
            status_output_sha256 = (
                store.write_public_report(args.output) if args.output else None
            )
            output = {
                "decision": report.decision.value,
                "counts": report.counts.model_dump(mode="json"),
                "criteria": report.criteria.model_dump(mode="json"),
                "blockers": report.blockers,
                "output_sha256": status_output_sha256,
            }
    except (M9ObservationError, OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        if store is not None:
            store.close()
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
