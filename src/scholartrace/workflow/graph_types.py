"""Dependency protocols shared by M3 graph services."""

from __future__ import annotations

from typing import Protocol

from scholartrace.contracts import ResearchPlan
from scholartrace.workflow.models import SearchBackendResult


class SearchBackend(Protocol):
    async def search(
        self,
        *,
        query: str,
        plan: ResearchPlan,
        round_index: int,
        idempotency_key: str,
    ) -> SearchBackendResult: ...


class PaperWorker(Protocol):
    async def analyze(
        self,
        *,
        task_id: str,
        canonical_paper_id: str,
        plan: ResearchPlan,
        idempotency_key: str,
    ) -> dict[str, object]: ...
