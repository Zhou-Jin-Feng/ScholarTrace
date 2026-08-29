"""SQLite persistence for canonical-paper to active DocuMind index bindings."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from scholartrace.contracts import DocuMindBinding


class BindingConflictError(RuntimeError):
    """The active binding changed without an explicit compare-and-swap."""


class DocuMindBindingRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documind_bindings (
                    canonical_paper_id TEXT PRIMARY KEY,
                    document_key TEXT NOT NULL,
                    index_id TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    documind_version TEXT NOT NULL,
                    retrieval_schema_version TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def get(self, canonical_paper_id: str) -> DocuMindBinding | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT canonical_paper_id, document_key, index_id, source_sha256,
                       documind_version, retrieval_schema_version
                FROM documind_bindings
                WHERE canonical_paper_id = ?
                """,
                (canonical_paper_id,),
            ).fetchone()
        return self._binding_from_row(row) if row is not None else None

    def put(
        self,
        binding: DocuMindBinding,
        *,
        expected_previous_index_id: str | None = None,
    ) -> DocuMindBinding:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT canonical_paper_id, document_key, index_id, source_sha256,
                       documind_version, retrieval_schema_version
                FROM documind_bindings
                WHERE canonical_paper_id = ?
                """,
                (binding.canonical_paper_id,),
            ).fetchone()
            if row is None:
                if expected_previous_index_id is not None:
                    raise BindingConflictError("cannot compare-and-swap a missing binding")
                self._insert(connection, binding)
                return binding

            current = self._binding_from_row(row)
            if current == binding:
                return current
            if expected_previous_index_id is None or current.index_id != expected_previous_index_id:
                raise BindingConflictError("active DocuMind binding changed unexpectedly")
            if current.document_key != binding.document_key:
                raise BindingConflictError("compare-and-swap cannot change document identity")
            connection.execute(
                """
                UPDATE documind_bindings
                SET index_id = ?, source_sha256 = ?, documind_version = ?,
                    retrieval_schema_version = ?, updated_at = ?
                WHERE canonical_paper_id = ? AND index_id = ?
                """,
                (
                    binding.index_id,
                    binding.source_sha256,
                    binding.documind_version,
                    binding.retrieval_schema_version,
                    datetime.now(UTC).isoformat(),
                    binding.canonical_paper_id,
                    expected_previous_index_id,
                ),
            )
            if connection.total_changes != 1:
                raise BindingConflictError("active DocuMind binding update lost a race")
            return binding

    @staticmethod
    def _insert(connection: sqlite3.Connection, binding: DocuMindBinding) -> None:
        connection.execute(
            """
            INSERT INTO documind_bindings (
                canonical_paper_id, document_key, index_id, source_sha256,
                documind_version, retrieval_schema_version, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                binding.canonical_paper_id,
                binding.document_key,
                binding.index_id,
                binding.source_sha256,
                binding.documind_version,
                binding.retrieval_schema_version,
                datetime.now(UTC).isoformat(),
            ),
        )

    @staticmethod
    def _binding_from_row(row: sqlite3.Row) -> DocuMindBinding:
        return DocuMindBinding(
            canonical_paper_id=row["canonical_paper_id"],
            document_key=row["document_key"],
            index_id=row["index_id"],
            source_sha256=row["source_sha256"],
            documind_version=row["documind_version"],
            retrieval_schema_version=row["retrieval_schema_version"],
        )
