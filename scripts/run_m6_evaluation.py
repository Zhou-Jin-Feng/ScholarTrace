"""Generate the sanitized M6 B0-B4 delivery matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scholartrace.delivery.evaluation import build_m6_evaluation_matrix


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "artifacts/reports/m6_b0_b4_delivery_matrix.json",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = build_m6_evaluation_matrix(root=root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "schema_version": report.schema_version,
                "overall_status": report.overall_status,
                "phases": [item.model_dump(mode="json") for item in report.phases],
                "blocking_reason_count": len(report.blocking_reasons),
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
