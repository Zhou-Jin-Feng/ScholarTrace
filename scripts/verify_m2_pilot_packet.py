"""Read-only validation of recorded private M2 Evidence, not a new online run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scholartrace.scholargraph.pilot_input import load_m2_pilot_artifacts

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--paper-fixture",
        type=Path,
        default=ROOT / "tests/fixtures/documind/m2_three_papers.json",
    )
    parser.add_argument("--expected-claims", required=True, type=int)
    parser.add_argument("--expected-evidence", required=True, type=int)
    args = parser.parse_args()
    try:
        packet = load_m2_pilot_artifacts(
            report_path=args.report, paper_fixture_path=args.paper_fixture
        )
        valid = (
            packet.validation.outcome == "succeeded"
            and all(row.passed for row in packet.validation.results)
            and len(packet.claims) == args.expected_claims
            and len(packet.evidence) == args.expected_evidence
            and len(packet.papers) == len(packet.bindings) == 3
        )
    except (OSError, ValueError):
        print(json.dumps({"result": "FAIL", "reason": "missing_or_invalid_private_packet"}))
        return 1
    print(
        json.dumps(
            {
                "result": "PASS" if valid else "FAIL",
                "scope": "recorded_private_packet_validation_not_new_online_run",
                "claims": len(packet.claims),
                "evidence": len(packet.evidence),
                "papers": len(packet.papers),
                "bindings": len(packet.bindings),
                "paper_pool_sha256": packet.paper_pool_sha256,
                "source_report_sha256": packet.source_report_sha256,
            }
        )
    )
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
