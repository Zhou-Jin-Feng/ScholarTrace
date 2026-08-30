"""Ordered lifecycle gate for papers discovered through citation expansion."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from scholartrace.citations.models import CitationPaperLifecycle
from scholartrace.contracts import ArtifactRef, DocuMindBinding, Paper


class CitationLifecycleError(RuntimeError):
    """A citation-discovered paper attempted to skip or repeat a lifecycle gate."""


class CitationLifecycleGate:
    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._records: dict[str, CitationPaperLifecycle] = {}

    def register(self, paper: Paper) -> CitationPaperLifecycle:
        if paper.canonical_paper_id in self._records:
            raise CitationLifecycleError("citation paper is already registered")
        record = CitationPaperLifecycle(
            canonical_paper_id=paper.canonical_paper_id,
            paper=paper,
            stage="normalized",
            updated_at=self._clock(),
        )
        self._records[paper.canonical_paper_id] = record
        return record

    def mark_relevant(self, paper_id: str) -> CitationPaperLifecycle:
        return self._advance(paper_id, expected="normalized", stage="relevance_passed")

    def mark_access_resolved(self, paper_id: str, *, access_url: str) -> CitationPaperLifecycle:
        return self._advance(
            paper_id,
            expected="relevance_passed",
            stage="access_resolved",
            access_url=access_url,
        )

    def mark_acquired(
        self, paper_id: str, *, source_sha256: str
    ) -> CitationPaperLifecycle:
        return self._advance(
            paper_id,
            expected="access_resolved",
            stage="acquired",
            acquired_source_sha256=source_sha256,
        )

    def mark_ingested(
        self, paper_id: str, *, binding: DocuMindBinding
    ) -> CitationPaperLifecycle:
        return self._advance(
            paper_id,
            expected="acquired",
            stage="ingested",
            binding=binding,
        )

    def mark_analyzed(
        self, paper_id: str, *, analysis_ref: ArtifactRef
    ) -> CitationPaperLifecycle:
        return self._advance(
            paper_id,
            expected="ingested",
            stage="analyzed",
            analysis_ref=analysis_ref,
        )

    def terminate(
        self,
        paper_id: str,
        *,
        outcome: str,
        public_reason: str,
    ) -> CitationPaperLifecycle:
        if outcome not in {"rejected", "failed"}:
            raise ValueError("lifecycle outcome must be rejected or failed")
        current = self.get(paper_id)
        if current.stage in {"analyzed", "rejected", "failed"}:
            raise CitationLifecycleError("terminal lifecycle record cannot transition")
        updated = current.model_copy(
            update={
                "stage": outcome,
                "public_reason": public_reason,
                "updated_at": self._clock(),
            }
        )
        validated = CitationPaperLifecycle.model_validate(updated.model_dump())
        self._records[paper_id] = validated
        return validated

    def can_analyze(self, paper_id: str) -> bool:
        return self.get(paper_id).stage == "ingested"

    def can_verify(self, paper_id: str) -> bool:
        return self.get(paper_id).stage == "analyzed"

    def get(self, paper_id: str) -> CitationPaperLifecycle:
        try:
            return self._records[paper_id]
        except KeyError as exc:
            raise CitationLifecycleError("citation paper is not registered") from exc

    def records(self) -> list[CitationPaperLifecycle]:
        return [self._records[key] for key in sorted(self._records)]

    def _advance(
        self,
        paper_id: str,
        *,
        expected: str,
        stage: str,
        **updates: object,
    ) -> CitationPaperLifecycle:
        current = self.get(paper_id)
        if current.stage != expected:
            raise CitationLifecycleError(
                f"citation lifecycle expected {expected}, found {current.stage}"
            )
        updated = current.model_copy(
            update={"stage": stage, "updated_at": self._clock(), **updates}
        )
        validated = CitationPaperLifecycle.model_validate(updated.model_dump())
        self._records[paper_id] = validated
        return validated
