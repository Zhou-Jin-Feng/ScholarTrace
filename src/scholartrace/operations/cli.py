"""Installed command-line entry points for local data operations."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from scholartrace.operations.backup import backup_data, restore_data


def backup_main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Create a verified ScholarTrace data backup archive."
    )
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    result = backup_data(data_dir=args.data_dir, output_path=args.output)
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))


def restore_main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Restore a verified backup into a new ScholarTrace data directory."
    )
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--target-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    result = restore_data(archive_path=args.archive, target_dir=args.target_dir)
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
