"""SQLite business stores kept separate from LangGraph checkpoints."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from scholartrace.contracts import ArtifactRef, BudgetLimits, BudgetUsage
from scholartrace.workflow.models import PersistedEvent


class ArtifactConflictError(RuntimeError):
    """A stable artifact ID was reused with different content."""


class WorkflowBudgetExceededError(RuntimeError):
    """A new effect would exceed the approved research budget."""


class RuntimeEffectConflictError(RuntimeError):
    """A stable runtime key was reused for a different effect or event."""


def _canonical_json(payload: object) -> str:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ArtifactStore:
    """Content-verified idempotent Artifact storage."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                artifact_type TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def put(self, *, artifact_id: str, artifact_type: str, payload: object) -> ArtifactRef:
        serialized = _canonical_json(payload)
        content_sha256 = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        created_at = datetime.now(UTC).isoformat()
        with self._connection:
            existing = self._connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if existing is None:
                self._connection.execute(
                    """
                    INSERT INTO artifacts(
                        artifact_id, artifact_type, content_sha256, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (artifact_id, artifact_type, content_sha256, serialized, created_at),
                )
            elif (
                existing["artifact_type"] != artifact_type
                or existing["content_sha256"] != content_sha256
            ):
                raise ArtifactConflictError(f"artifact content conflict: {artifact_id}")
            else:
                created_at = str(existing["created_at"])
        return ArtifactRef(
            artifact_id=artifact_id,
            artifact_type=artifact_type,  # type: ignore[arg-type]
            content_sha256=content_sha256,
            storage_uri=f"sqlite://{self.path.as_posix()}#artifacts/{artifact_id}",
            created_at=datetime.fromisoformat(created_at),
        )

    def get_json(self, artifact_id: str) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT payload_json FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        payload: dict[str, Any] = json.loads(str(row["payload_json"]))
        return payload

    def get_ref(self, artifact_id: str) -> ArtifactRef | None:
        row = self._connection.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        if row is None:
            return None
        return ArtifactRef(
            artifact_id=str(row["artifact_id"]),
            artifact_type=str(row["artifact_type"]),  # type: ignore[arg-type]
            content_sha256=str(row["content_sha256"]),
            storage_uri=f"sqlite://{self.path.as_posix()}#artifacts/{row['artifact_id']}",
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS count FROM artifacts").fetchone()
        return int(row["count"])


_USAGE_FIELDS = tuple(BudgetUsage.model_fields)


class RuntimeLedger:
    """Idempotent budget charges and durable business events."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        usage_columns = ",\n".join(f"{field} REAL NOT NULL" for field in _USAGE_FIELDS)
        self._connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS budget_effects (
                effect_key TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                {usage_columns},
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                stable_key TEXT NOT NULL UNIQUE,
                task_id TEXT NOT NULL,
                node TEXT NOT NULL,
                kind TEXT NOT NULL,
                artifact_id TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def charge(
        self,
        *,
        effect_key: str,
        task_id: str,
        delta: BudgetUsage,
        limits: BudgetLimits,
    ) -> BudgetUsage:
        with self._lock:
            with self._connection:
                existing = self._connection.execute(
                    "SELECT * FROM budget_effects WHERE effect_key = ?", (effect_key,)
                ).fetchone()
                if existing is not None:
                    same_delta = all(
                        float(existing[field]) == float(getattr(delta, field))
                        for field in _USAGE_FIELDS
                    )
                    if existing["task_id"] != task_id or not same_delta:
                        raise RuntimeEffectConflictError(
                            f"budget effect content conflict: {effect_key}"
                        )
                    return self.usage(task_id)
                current = self.usage(task_id)
                combined = BudgetUsage(
                    **{
                        field: getattr(current, field) + getattr(delta, field)
                        for field in _USAGE_FIELDS
                    }
                )
                violations = self._violations(combined, limits)
                if violations:
                    raise WorkflowBudgetExceededError(
                        "workflow budget exceeded: " + ", ".join(violations)
                    )
                values = [getattr(delta, field) for field in _USAGE_FIELDS]
                placeholders = ", ".join("?" for _ in range(3 + len(values)))
                columns = ", ".join(("effect_key", "task_id", *_USAGE_FIELDS, "created_at"))
                self._connection.execute(
                    f"INSERT INTO budget_effects({columns}) VALUES ({placeholders})",
                    (effect_key, task_id, *values, datetime.now(UTC).isoformat()),
                )
            return combined

    def confirms_projected_usage(
        self, *, effect_key: str, task_id: str, delta: BudgetUsage,
    ) -> bool:
        """Read an exact durable projection without charging or repairing it."""
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM budget_effects WHERE effect_key=?", (effect_key,),
            ).fetchone()
            return row is not None and row["task_id"] == task_id and all(
                float(row[field]) == float(getattr(delta, field)) for field in _USAGE_FIELDS
            )

    def project_confirmed_usage(
        self, *, effect_key: str, task_id: str, delta: BudgetUsage,
    ) -> None:
        """Mirror a durable journal measurement, never authorize another call.

        A measured overrun must remain visible even when it exceeds a plan.
        Admission belongs to the effect journal; this projection has no budget
        granting semantics and refuses a conflicting retry.
        """
        if not effect_key.startswith("journal:"):
            raise ValueError("journal projections require a distinct stable-key namespace")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    "SELECT * FROM budget_effects WHERE effect_key=?", (effect_key,),
                ).fetchone()
                if existing is not None:
                    if existing["task_id"] != task_id or any(
                        float(existing[field]) != float(getattr(delta, field))
                        for field in _USAGE_FIELDS
                    ):
                        raise RuntimeEffectConflictError("journal usage projection conflicts")
                else:
                    columns = ",".join(("effect_key", "task_id", *_USAGE_FIELDS, "created_at"))
                    placeholders = ",".join("?" for _ in range(3 + len(_USAGE_FIELDS)))
                    self._connection.execute(
                        f"INSERT INTO budget_effects({columns}) VALUES ({placeholders})",
                        (effect_key, task_id, *(getattr(delta, f) for f in _USAGE_FIELDS),
                         datetime.now(UTC).isoformat()),
                    )
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    def usage(self, task_id: str) -> BudgetUsage:
        with self._lock:
            expressions = ", ".join(
                f"COALESCE(SUM({field}), 0) AS {field}" for field in _USAGE_FIELDS
            )
            row = self._connection.execute(
                f"SELECT {expressions} FROM budget_effects WHERE task_id = ?", (task_id,)
            ).fetchone()
            return BudgetUsage.model_validate(dict(row))

    def append_event(
        self,
        *,
        stable_key: str,
        task_id: str,
        node: str,
        kind: str,
        artifact_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> PersistedEvent:
        serialized = _canonical_json(payload or {})
        created_at = datetime.now(UTC).isoformat()
        with self._lock:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO events(
                        stable_key, task_id, node, kind, artifact_id, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (stable_key, task_id, node, kind, artifact_id, serialized, created_at),
                )
                row = self._connection.execute(
                    "SELECT * FROM events WHERE stable_key = ?", (stable_key,)
                ).fetchone()
                if (
                    row["task_id"] != task_id
                    or row["node"] != node
                    or row["kind"] != kind
                    or row["artifact_id"] != artifact_id
                    or row["payload_json"] != serialized
                ):
                    raise RuntimeEffectConflictError(f"event content conflict: {stable_key}")
            return self._event_from_row(row)

    def replay(self, *, task_id: str, last_event_id: str | None = None) -> list[PersistedEvent]:
        with self._lock:
            last_sequence = self._event_sequence(task_id=task_id, event_id=last_event_id)
            rows = self._connection.execute(
                """
                SELECT * FROM events
                WHERE task_id = ? AND sequence > ?
                ORDER BY sequence ASC
                """,
                (task_id, last_sequence),
            ).fetchall()
            return [self._event_from_row(row) for row in rows]

    def event_count(self, task_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS count FROM events WHERE task_id = ?", (task_id,)
            ).fetchone()
            return int(row["count"])

    def event_kind(self, *, task_id: str, event_id: str) -> str | None:
        """Return a task-owned event kind for reconnect terminal detection."""

        with self._lock:
            sequence = self._event_sequence(task_id=task_id, event_id=event_id)
            row = self._connection.execute(
                "SELECT kind FROM events WHERE sequence = ? AND task_id = ?",
                (sequence, task_id),
            ).fetchone()
            return None if row is None else str(row["kind"])

    def _event_sequence(self, *, task_id: str, event_id: str | None) -> int:
        if event_id is None:
            return 0
        prefix, separator, raw_sequence = event_id.rpartition(":")
        if not separator or prefix != "event" or not raw_sequence.isdigit():
            raise ValueError("invalid Last-Event-ID")
        sequence = int(raw_sequence)
        row = self._connection.execute(
            "SELECT task_id FROM events WHERE sequence = ?", (sequence,)
        ).fetchone()
        if row is None or row["task_id"] != task_id:
            raise ValueError("invalid Last-Event-ID")
        return sequence

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> PersistedEvent:
        sequence = int(row["sequence"])
        return PersistedEvent(
            event_id=f"event:{sequence}",
            sequence=sequence,
            task_id=str(row["task_id"]),
            node=str(row["node"]),
            kind=str(row["kind"]),
            artifact_id=row["artifact_id"],
            payload=json.loads(str(row["payload_json"])),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _violations(usage: BudgetUsage, limits: BudgetLimits) -> list[str]:
        pairs = (
            (usage.queries, limits.max_queries, "queries"),
            (usage.candidate_papers, limits.max_candidate_papers, "candidate_papers"),
            (usage.fulltext_papers, limits.max_fulltext_papers, "fulltext_papers"),
            (
                usage.rag_calls,
                limits.max_rag_calls_per_paper * limits.max_fulltext_papers,
                "rag_calls",
            ),
            (usage.llm_input_tokens, limits.max_llm_input_tokens, "llm_input_tokens"),
            (usage.llm_output_tokens, limits.max_llm_output_tokens, "llm_output_tokens"),
            (usage.api_calls, limits.max_api_calls, "api_calls"),
            (usage.model_calls, limits.max_model_calls, "model_calls"),
            (usage.external_cost_cny, limits.max_cost_cny, "external_cost_cny"),
            (usage.elapsed_seconds, limits.max_duration_seconds, "elapsed_seconds"),
        )
        violations = [name for actual, maximum, name in pairs if actual > maximum]
        if (
            limits.max_total_tokens is not None
            and usage.llm_input_tokens + usage.llm_output_tokens > limits.max_total_tokens
        ):
            violations.append("total_tokens")
        return violations
