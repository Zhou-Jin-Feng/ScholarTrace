"""Idempotent, timeout-bounded execution for paper analysis effects."""

from __future__ import annotations

import asyncio

from scholartrace.contracts import ArtifactRef, BudgetUsage, ResearchPlan
from scholartrace.workflow.graph_types import PaperWorker
from scholartrace.workflow.models import PaperWorkerArtifact
from scholartrace.workflow.storage import ArtifactStore, RuntimeLedger


class IdempotentPaperWorkerRunner:
    """Deduplicate local delivery and propagate a stable key to external effects."""

    def __init__(
        self,
        *,
        worker: PaperWorker,
        artifacts: ArtifactStore,
        ledger: RuntimeLedger,
        max_concurrency: int,
        timeout_seconds: float,
    ) -> None:
        self.worker = worker
        self.artifacts = artifacts
        self.ledger = ledger
        self.max_concurrency = max_concurrency
        self.timeout_seconds = timeout_seconds
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._effect_locks: dict[str, asyncio.Lock] = {}

    async def run(
        self,
        *,
        task_id: str,
        canonical_paper_id: str,
        plan: ResearchPlan,
    ) -> tuple[ArtifactRef, list[str]]:
        artifact_id = f"artifact:m3:{task_id}:paper:{canonical_paper_id}"
        effect_key = f"effect:m3:{task_id}:paper:{canonical_paper_id}"
        lock = self._effect_locks.setdefault(effect_key, asyncio.Lock())
        async with lock:
            existing = self.artifacts.get_ref(artifact_id)
            if existing is not None:
                persisted = PaperWorkerArtifact.model_validate(
                    self.artifacts.get_json(artifact_id)
                )
                failed = [] if persisted.status == "succeeded" else [canonical_paper_id]
                return existing, failed

            self.ledger.charge(
                effect_key=effect_key,
                task_id=task_id,
                delta=BudgetUsage(fulltext_papers=1),
                limits=plan.budget.limits,
            )
            limit = min(self.max_concurrency, plan.budget.limits.max_concurrency)
            semaphore = self._semaphores.setdefault(task_id, asyncio.Semaphore(limit))
            try:
                async with semaphore:
                    output = await asyncio.wait_for(
                        self.worker.analyze(
                            task_id=task_id,
                            canonical_paper_id=canonical_paper_id,
                            plan=plan,
                            idempotency_key=effect_key,
                        ),
                        timeout=self.timeout_seconds,
                    )
                persisted = PaperWorkerArtifact(
                    task_id=task_id,
                    canonical_paper_id=canonical_paper_id,
                    status="succeeded",
                    output=output,
                )
                artifact_type = "paper_card"
                failed = []
            except TimeoutError:
                persisted = PaperWorkerArtifact(
                    task_id=task_id,
                    canonical_paper_id=canonical_paper_id,
                    status="timed_out",
                    public_reason="worker_timeout",
                )
                artifact_type = "tool_run"
                failed = [canonical_paper_id]
            except Exception:
                persisted = PaperWorkerArtifact(
                    task_id=task_id,
                    canonical_paper_id=canonical_paper_id,
                    status="failed",
                    public_reason="worker_failed",
                )
                artifact_type = "tool_run"
                failed = [canonical_paper_id]
            ref = self.artifacts.put(
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                payload=persisted,
            )
            self.ledger.append_event(
                stable_key=f"event:m3:{task_id}:paper:{canonical_paper_id}",
                task_id=task_id,
                node="paper_worker",
                kind=f"paper_worker_{persisted.status}",
                artifact_id=ref.artifact_id,
            )
            return ref, failed
