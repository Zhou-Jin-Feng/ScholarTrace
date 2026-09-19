"""Run a bounded live OpenAlex citation expansion and persist safe metrics."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from scholartrace.citations.live_smoke import run_openalex_live_smoke
from scholartrace.citations.models import CitationSeed
from scholartrace.search.storage import write_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEEDS = ROOT / "tests" / "fixtures" / "m4" / "openalex_live_seeds.json"
DEFAULT_CACHE_ROOT = ROOT / "artifacts" / "m4-openalex-live"
DEFAULT_OUTPUT = ROOT / "artifacts" / "reports" / "m4_openalex_live_smoke.json"


def _load_seeds(path: Path, *, limit: int) -> list[CitationSeed]:
    payload = json.loads(path.read_text("utf-8"))
    return [CitationSeed.model_validate(item) for item in payload["seeds"][:limit]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=Path, default=DEFAULT_SEEDS)
    parser.add_argument("--seed-limit", type=int, choices=(1, 2), default=2)
    parser.add_argument("--max-discovered-papers", type=int, default=10)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    run_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    cache_dir = args.cache_dir or DEFAULT_CACHE_ROOT / run_stamp / "cache"
    seeds = _load_seeds(args.seeds, limit=args.seed_limit)
    summary = asyncio.run(
        run_openalex_live_smoke(
            seeds=seeds,
            cache_dir=cache_dir,
            api_key=os.getenv("OPENALEX_API_KEY"),
            max_discovered_papers=args.max_discovered_papers,
        )
    )
    write_json(args.output, summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
