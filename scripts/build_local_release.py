"""Build the deterministic ScholarTrace local source release archive."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scholartrace.operations.release import build_release_archive

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = build_release_archive(root=ROOT, output_path=args.output)
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
