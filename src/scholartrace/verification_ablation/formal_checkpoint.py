"""Request-level durable checkpoint support for SA-05 formal runs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from scholartrace.search.storage import write_json

from .models import (
    AblationAttemptRecord,
    AblationExecutionManifest,
    AblationPrivateRow,
    AblationUsage,
    AblationVariant,
    ablation_request_identity_sha256,
)


class FormalCheckpointRecorder:
    """Persist intent/attempt state before dispatch and results after completion."""

    def __init__(
        self,
        *,
        path: Path,
        manifest: AblationExecutionManifest,
        run_id: str,
        attempts: list[AblationAttemptRecord] | None = None,
        rows: list[AblationPrivateRow] | None = None,
        usage_by_variant: Mapping[AblationVariant, AblationUsage] | None = None,
    ) -> None:
        self.path = path
        self.manifest = manifest
        self.run_id = run_id
        self.manifest_sha256 = manifest.stable_sha256()
        self.attempts = list(attempts or [])
        self.rows = list(rows or [])
        self.usage_by_variant = dict(usage_by_variant or {})

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        expected_manifest: AblationExecutionManifest | None = None,
        allow_budget_policy_migration: bool = False,
    ) -> FormalCheckpointRecorder:
        payload = json.loads(path.read_text(encoding="utf-8"))
        source_manifest = AblationExecutionManifest.model_validate(payload["manifest"])
        manifest = source_manifest
        manifest_sha256 = payload.get("manifest_sha256")
        migrated = False
        if expected_manifest is not None and manifest_sha256 != expected_manifest.stable_sha256():
            if not allow_budget_policy_migration or (
                source_manifest.execution_identity_sha256()
                != expected_manifest.execution_identity_sha256()
            ):
                raise ValueError("formal checkpoint manifest hash mismatch")
            manifest = expected_manifest
            migrated = True
        elif manifest_sha256 != manifest.stable_sha256():
            raise ValueError("formal checkpoint manifest hash mismatch")
        target_configuration = manifest.configuration_sha256()
        rows = [AblationPrivateRow.model_validate(item) for item in payload.get("rows", [])]
        attempts = [
            AblationAttemptRecord.model_validate(item)
            for item in payload.get("attempts", [])
        ]
        if migrated:
            rows = [
                row.model_copy(
                    update={
                        "result": row.result.model_copy(
                            update={"configuration_sha256": target_configuration}
                        )
                    }
                )
                for row in rows
            ]
            attempts = [
                attempt.model_copy(
                    update={
                        "configuration_sha256": target_configuration,
                        "request_identity_sha256": (
                            ablation_request_identity_sha256(
                                run_id=attempt.run_id,
                                question_id=attempt.question_id,
                                variant=attempt.variant,
                                operation=attempt.operation,
                                subject_id=attempt.subject_id,
                                attempt_number=attempt.attempt_number,
                                configuration_sha256=target_configuration,
                                frozen_input_sha256=attempt.frozen_input_sha256,
                            )
                            if attempt.request_identity_sha256 is not None
                            else None
                        ),
                    }
                )
                for attempt in attempts
            ]
        return cls(
            path=path,
            manifest=manifest,
            run_id=str(payload["run_id"]),
            attempts=attempts,
            rows=rows,
            usage_by_variant={
                variant: AblationUsage.model_validate(item)
                for variant, item in payload.get("usage_by_variant", {}).items()
            },
        )

    def record_attempt(self, attempt: AblationAttemptRecord) -> None:
        self._validate_identity(attempt.run_id, attempt.configuration_sha256)
        if attempt.request_identity_sha256 is not None:
            expected_request_identity = ablation_request_identity_sha256(
                run_id=attempt.run_id,
                question_id=attempt.question_id,
                variant=attempt.variant,
                operation=attempt.operation,
                subject_id=attempt.subject_id,
                attempt_number=attempt.attempt_number,
                configuration_sha256=attempt.configuration_sha256,
                frozen_input_sha256=attempt.frozen_input_sha256,
            )
            if attempt.request_identity_sha256 != expected_request_identity:
                raise ValueError("formal checkpoint request identity drifted")
        self.attempts = [
            item for item in self.attempts if item.attempt_id != attempt.attempt_id
        ]
        self.attempts.append(attempt)
        self._write()

    def record_row(
        self,
        row: AblationPrivateRow,
        usages: Mapping[AblationVariant, AblationUsage],
    ) -> None:
        self._validate_identity(
            row.result.run_id,
            row.result.configuration_sha256,
        )
        key = (row.result.question_id, row.result.variant)
        self.rows = [
            item
            for item in self.rows
            if (item.result.question_id, item.result.variant) != key
        ]
        self.rows.append(row)
        self.usage_by_variant = dict(usages)
        self._write()

    def _validate_identity(self, run_id: str, configuration_sha256: str) -> None:
        if run_id != self.run_id:
            raise ValueError("formal checkpoint run ID drifted")
        if configuration_sha256 != self.manifest.configuration_sha256():
            raise ValueError("formal checkpoint configuration hash drifted")

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json(
            self.path,
            {
                "schema_version": "1.0",
                "purpose": "scholartrace-sa05-private-formal-checkpoint",
                "run_id": self.run_id,
                "manifest": self.manifest.model_dump(mode="json"),
                "manifest_sha256": self.manifest_sha256,
                "attempts": [item.model_dump(mode="json") for item in self.attempts],
                "rows": [item.model_dump(mode="json") for item in self.rows],
                "usage_by_variant": {
                    variant: usage.model_dump(mode="json")
                    for variant, usage in self.usage_by_variant.items()
                },
            },
        )
