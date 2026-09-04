"""Restore a verified ScholarTrace backup into a new data directory."""

from __future__ import annotations

from scholartrace.operations.cli import restore_main


def main() -> None:
    restore_main()


if __name__ == "__main__":
    main()
