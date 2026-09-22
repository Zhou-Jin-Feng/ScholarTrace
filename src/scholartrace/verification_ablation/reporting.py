"""Private/public serialization for SA-03 ablation runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scholartrace.search.storage import write_json

from .models import AblationError, AblationPrivateArchive


def require_private_path(path: Path) -> Path:
    resolved = path.resolve()
    if "agent" not in {part.lower() for part in resolved.parts}:
        raise AblationError("raw ablation artifacts must be written below agent/")
    return resolved


def public_payload(archive: AblationPrivateArchive) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "purpose": "scholartrace-sa03-verification-ablation-sanitized-run",
        "quality_scope": (
            "Fixture infrastructure only; raw reports, Claim text, Evidence quotes, "
            "and verifier reasons remain private."
        ),
        "manifest": archive.manifest.model_dump(mode="json"),
        "manifest_sha256": archive.manifest_sha256,
        "usage_by_variant": {
            variant: usage.model_dump(mode="json")
            for variant, usage in archive.usage_by_variant.items()
        },
        "result_usage_by_variant": {
            variant: usage.model_dump(mode="json")
            for variant, usage in archive.result_usage_by_variant.items()
        },
        "usage_scope": "all attempts; result_usage_by_variant contains final paired results",
        "runs": [row.result.model_dump(mode="json") for row in archive.rows],
        "raw_answers_stored_publicly": False,
        "verifier_reasons_stored_publicly": False,
        "private_artifacts_required_for_review": True,
        "notes": [
            "V-off uses an experiment-only unverified state and never enters M4 ReportGateResult.",
            "Zero model/provider calls in the fixture do not establish semantic quality.",
            "Failures remain rows with status and error code; they are not removed from coverage.",
        ],
    }


def write_artifacts(
    *,
    archive: AblationPrivateArchive,
    private_path: Path,
    public_path: Path,
) -> None:
    private_destination = require_private_path(private_path)
    if private_destination == public_path.resolve():
        raise AblationError("private and public ablation outputs must differ")
    write_json(private_destination, archive.model_dump(mode="json"))
    write_json(public_path, public_payload(archive))
