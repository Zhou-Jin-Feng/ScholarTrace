"""Durable M6 task metadata and export artifact storage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scholartrace.delivery.migrations import migrate
from scholartrace.delivery.models import DemoMode, ExecutionMode, TaskPhase, TaskStatus
from scholartrace.delivery.transactions import transaction


class InvalidCursorError(ValueError):
    """The supplied list cursor could not be decoded."""

    code = "invalid_cursor"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class DeliveryStore:
    """SQLite store with idempotent task creation and immutable artifacts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                thread_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                question TEXT NOT NULL,
                status TEXT NOT NULL,
                phase TEXT NOT NULL,
                demo_mode TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                degradations_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                idempotency_key TEXT PRIMARY KEY,
                request_sha256 TEXT NOT NULL,
                task_id TEXT NOT NULL REFERENCES tasks(task_id)
            );
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL REFERENCES tasks(task_id),
                artifact_type TEXT NOT NULL,
                media_type TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                content BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()
        # Bring older databases up to the current schema. Transactional: a
        # failure leaves the previous version intact rather than half-migrated.
        self.schema_version = migrate(self.connection)

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def create_task(
        self,
        *,
        task_id: str,
        thread_id: str,
        title: str,
        question: str,
        demo_mode: DemoMode,
        idempotency_key: str | None = None,
        request_sha256: str | None = None,
        execution_mode: ExecutionMode = ExecutionMode.DEMO,
    ) -> dict[str, Any]:
        with self._lock:
            if idempotency_key is not None:
                existing = self.connection.execute(
                    "SELECT task_id, request_sha256 FROM idempotency_keys "
                    "WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if request_sha256 != existing["request_sha256"]:
                        raise ValueError("idempotency key reused with a different request")
                    return self.get_task(str(existing["task_id"]))

            timestamp = _now()
            with transaction(self.connection):
                self.connection.execute(
                    """
                    INSERT INTO tasks(
                        task_id, thread_id, title, question, status, phase, demo_mode,
                        created_at, updated_at, metrics_json, degradations_json, execution_mode
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        thread_id,
                        title,
                        question,
                        TaskStatus.WAITING_APPROVAL.value,
                        TaskPhase.PLAN.value,
                        demo_mode.value,
                        timestamp,
                        timestamp,
                        _json({}),
                        _json([]),
                        execution_mode.value,
                    ),
                )
                if idempotency_key is not None and request_sha256 is not None:
                    self.connection.execute(
                        "INSERT INTO idempotency_keys("
                        "idempotency_key, request_sha256, task_id) VALUES (?, ?, ?)",
                        (idempotency_key, request_sha256, task_id),
                    )
            return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            result = dict(row)
            result["metrics"] = json.loads(str(result.pop("metrics_json")))
            result["degradations"] = json.loads(str(result.pop("degradations_json")))
            return result

    def update_task(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        phase: TaskPhase | None = None,
        metrics: dict[str, object] | None = None,
        degradations: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            current = self.get_task(task_id)
            with transaction(self.connection):
                self.connection.execute(
                    """
                    UPDATE tasks SET status = ?, phase = ?, updated_at = ?,
                        metrics_json = ?, degradations_json = ?
                    WHERE task_id = ?
                    """,
                    (
                        (status or TaskStatus(str(current["status"]))).value,
                        (phase or TaskPhase(str(current["phase"]))).value,
                        _now(),
                        _json(metrics if metrics is not None else current["metrics"]),
                        _json(
                            degradations if degradations is not None else current["degradations"]
                        ),
                        task_id,
                    ),
                )
            return self.get_task(task_id)

    def add_artifact(
        self,
        *,
        task_id: str,
        artifact_id: str,
        artifact_type: str,
        media_type: str,
        content: bytes,
    ) -> dict[str, Any]:
        with self._lock:
            digest = hashlib.sha256(content).hexdigest()
            timestamp = _now()
            with transaction(self.connection):
                existing = self.connection.execute(
                    "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
                ).fetchone()
                if existing is None:
                    self.connection.execute(
                        """
                        INSERT INTO artifacts(
                            artifact_id, task_id, artifact_type, media_type,
                            content_sha256, content, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            artifact_id,
                            task_id,
                            artifact_type,
                            media_type,
                            digest,
                            content,
                            timestamp,
                        ),
                    )
                elif (
                    existing["task_id"] != task_id
                    or existing["artifact_type"] != artifact_type
                    or existing["content_sha256"] != digest
                ):
                    raise ValueError(f"artifact conflict: {artifact_id}")
            return self.get_artifact(artifact_id, include_content=False)

    def list_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at, artifact_id",
                (task_id,),
            ).fetchall()
            return [self._artifact_row(row, include_content=False) for row in rows]

    # ---- Task list with keyset pagination (T13) ---------------------------

    @staticmethod
    def encode_cursor(created_at: str, task_id: str) -> str:
        raw = json.dumps({"c": created_at, "i": task_id}, separators=(",", ":"))
        return urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")

    @staticmethod
    def decode_cursor(cursor: str) -> tuple[str, str]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            payload = json.loads(urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
            return str(payload["c"]), str(payload["i"])
        except Exception as exc:  # narrow surface: any malformed cursor is a 400
            raise InvalidCursorError("cursor is not decodable") from exc

    def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Return one page ordered by ``(created_at DESC, task_id DESC)``.

        Keyset, not OFFSET: concurrent inserts must not cause a page to skip or
        repeat rows while the user is paging through history.
        """

        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if cursor is not None:
            created_at, task_id = self.decode_cursor(cursor)
            clauses.append("(created_at < ? OR (created_at = ? AND task_id < ?))")
            params.extend([created_at, created_at, task_id])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self.connection.execute(
                f"SELECT * FROM tasks{where} ORDER BY created_at DESC, task_id DESC LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            count_params = params[:1] if status is not None else []
            count_where = " WHERE status = ?" if status is not None else ""
            total = int(
                self.connection.execute(
                    f"SELECT COUNT(*) FROM tasks{count_where}", tuple(count_params)
                ).fetchone()[0]
            )
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = (
            self.encode_cursor(str(page[-1]["created_at"]), str(page[-1]["task_id"]))
            if has_more and page
            else None
        )
        return {
            "items": [self._task_row(row) for row in page],
            "next_cursor": next_cursor,
            "total_known": total,
        }

    def _task_row(self, row: sqlite3.Row) -> dict[str, Any]:
        keys = set(row.keys())
        return {
            "task_id": str(row["task_id"]),
            "thread_id": str(row["thread_id"]),
            "title": str(row["title"]),
            "question": str(row["question"]),
            "status": str(row["status"]),
            "phase": str(row["phase"]),
            "demo_mode": str(row["demo_mode"]),
            # Absent on databases migrated from v1; those rows really were demo.
            "execution_mode": str(row["execution_mode"]) if "execution_mode" in keys else "demo",
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "metrics": json.loads(row["metrics_json"]),
            "degradations": json.loads(row["degradations_json"]),
        }

    def get_artifact(self, artifact_id: str, *, include_content: bool = True) -> dict[str, Any]:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if row is None:
                raise KeyError(artifact_id)
            return self._artifact_row(row, include_content=include_content)

    @staticmethod
    def _artifact_row(row: sqlite3.Row, *, include_content: bool) -> dict[str, Any]:
        result: dict[str, Any] = {
            "artifact_id": str(row["artifact_id"]),
            "task_id": str(row["task_id"]),
            "artifact_type": str(row["artifact_type"]),
            "media_type": str(row["media_type"]),
            "content_sha256": str(row["content_sha256"]),
            "size_bytes": len(bytes(row["content"])),
            "created_at": str(row["created_at"]),
        }
        if include_content:
            result["content"] = bytes(row["content"])
        return result
