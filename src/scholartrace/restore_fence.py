"""Tree-local restore provenance; no automatic reconciliation or unlock."""

from __future__ import annotations

import os
from pathlib import Path

RESTORE_FENCE_NAME = ".scholartrace-restore-reconciliation-required"


def create_restore_fence(staging_root: Path) -> None:
    """Flush the fence before publishing the staging tree, without changing SQL."""
    with (staging_root / RESTORE_FENCE_NAME).open("xb") as handle:
        handle.write(b"Restored data: external dispatch requires reconciliation.\n")
        handle.flush()
        os.fsync(handle.fileno())


def is_restore_fenced(path: Path) -> bool:
    """Cover nested journals and both sides of aliases, not sibling data trees.

    Any marker entry fences the tree, regardless of contents or type. Only an
    absent entry is safe; permission/stat errors propagate to fail closed.
    """
    for parent in dict.fromkeys((*path.absolute().parents, *path.resolve().parents)):
        try:
            (parent / RESTORE_FENCE_NAME).lstat()
        except FileNotFoundError:
            continue
        return True
    return False
