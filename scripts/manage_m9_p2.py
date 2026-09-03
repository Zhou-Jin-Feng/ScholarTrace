#!/usr/bin/env python3
"""Manage the private M9-P2 prospective ledger and sanitized Gate A status."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scholartrace.scholargraph.prospective_gate import (
    M9P2CaptureSubmission,
    M9P2ConditionSnapshot,
    M9P2Error,
    M9P2GoldSubmission,
    M9P2Store,
    require_project_agent_path,
)
from scholartrace.search.storage import write_model

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "agent" / "m9-p2" / "prospective.sqlite"
DEFAULT_PREREGISTRATION = ROOT / "evaluation" / "m9" / "p2_preregistration.json"


def _private_json(path: Path) -> dict[str, Any]:
    payload = json.loads(require_project_agent_path(path, project_root=ROOT).read_text("utf-8"))
    if not isinstance(payload, dict):
        raise M9P2Error("private input must be a JSON object")
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--preregistration", type=Path, default=DEFAULT_PREREGISTRATION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture")
    capture.add_argument("--input", type=Path, required=True)
    capture.add_argument("--public-report", type=Path)

    gold = subparsers.add_parser("gold")
    gold.add_argument("--input", type=Path, required=True)
    gold.add_argument("--receipt", type=Path, required=True)
    gold.add_argument("--public-report", type=Path)

    condition = subparsers.add_parser("condition")
    condition.add_argument("--input", type=Path, required=True)
    condition.add_argument("--public-report", type=Path)

    status = subparsers.add_parser("status")
    status.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    store: M9P2Store | None = None
    try:
        store = M9P2Store(
            args.database,
            project_root=ROOT,
            preregistration_path=args.preregistration,
        )
        if args.command == "capture":
            capture_submission = M9P2CaptureSubmission.model_validate(_private_json(args.input))
            record_id, sequence_id, entry_sha256 = store.capture(capture_submission)
            report_sha256 = (
                store.write_public_report(args.public_report) if args.public_report else None
            )
            output = {
                "record_id": record_id,
                "sequence_id": sequence_id,
                "capture_entry_sha256": entry_sha256,
                "decision": store.public_report().decision.value,
                "public_report_sha256": report_sha256,
            }
        elif args.command == "gold":
            gold_submission = M9P2GoldSubmission.model_validate(_private_json(args.input))
            receipt = store.freeze_gold(gold_submission)
            receipt_path = require_project_agent_path(args.receipt, project_root=ROOT)
            write_model(receipt_path, receipt)
            report_sha256 = (
                store.write_public_report(args.public_report) if args.public_report else None
            )
            output = {
                "record_id": gold_submission.record_id,
                "gold_revision": gold_submission.expected_revision + 1,
                "gold_entry_sha256": receipt.gold_entry_sha256,
                "receipt_sha256": receipt.receipt_sha256,
                "decision": store.public_report().decision.value,
                "public_report_sha256": report_sha256,
            }
        elif args.command == "condition":
            snapshot = M9P2ConditionSnapshot.model_validate(_private_json(args.input))
            entry_sha256 = store.attach_condition(snapshot)
            report_sha256 = (
                store.write_public_report(args.public_report) if args.public_report else None
            )
            output = {
                "record_id": snapshot.record_id,
                "condition_entry_sha256": entry_sha256,
                "decision": store.public_report().decision.value,
                "public_report_sha256": report_sha256,
            }
        else:
            report = store.public_report()
            report_sha256 = store.write_public_report(args.output) if args.output else None
            output = {
                "decision": report.decision.value,
                "counts": report.counts.model_dump(mode="json"),
                "criteria": report.criteria.model_dump(mode="json"),
                "output_sha256": report_sha256,
            }
    except (M9P2Error, OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        if store is not None:
            store.close()
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
