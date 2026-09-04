"""Create a verified ScholarTrace data backup archive."""

from __future__ import annotations

from scholartrace.operations.cli import backup_main


def main() -> None:
    backup_main()


if __name__ == "__main__":
    main()
