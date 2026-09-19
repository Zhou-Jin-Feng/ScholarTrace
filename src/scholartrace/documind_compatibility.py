"""Explicit DocuMind service-version policy; schema versions are independent."""

from __future__ import annotations

import re

# Keep the existing 2.x response contract and add only the reviewed 3.0.0 release.
DOCUMIND_VERSION_PATTERN = r"^(?:2\.[1-9][0-9]*\.[0-9]+|3\.0\.0)$"


def supports_documind_retrieve(version: str) -> bool:
    return re.fullmatch(DOCUMIND_VERSION_PATTERN, version) is not None


def validate_documind_identity(version: str, commit: str) -> None:
    if not supports_documind_retrieve(version):
        raise ValueError("unsupported DocuMind provider version")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("provider identity requires a full lowercase commit SHA")
