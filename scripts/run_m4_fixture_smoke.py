"""Run and persist the zero-cost deterministic M4 reliability smoke."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from scholartrace.search.storage import write_json
from scholartrace.verification.fixture_smoke import run_fixture_smoke

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "m4" / "reliability_cases.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "reports" / "m4_reliability_fixture_smoke.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    summary = asyncio.run(run_fixture_smoke(args.fixture))
    write_json(args.output, summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
