"""Build an auditable M1 RunManifest from a search snapshot."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from scholartrace import __version__
from scholartrace.contracts import (
    ArtifactRef,
    Budget,
    BudgetLimits,
    BudgetUsage,
    RunManifest,
    ServiceBaseline,
)
from scholartrace.search.models import SearchSnapshot

BASELINE_TEMPLATE_VERSION = "m1-deterministic-extractive-v1"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_run_manifest(
    *,
    root: Path,
    snapshot: SearchSnapshot,
    snapshot_path: Path,
    snapshot_file_sha256: str,
    started_at: datetime,
    completed_at: datetime,
    git_commit: str,
    worktree_dirty: bool,
    source_tree_sha256: str,
) -> RunManifest:
    model_policy_payload = json.loads(
        (root / "contracts" / "examples" / "m0_bundle.json").read_text("utf-8")
    )["ModelRoutingPolicy"]
    model_policy_sha = hashlib.sha256(
        json.dumps(
            model_policy_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    prompt_sha = hashlib.sha256(BASELINE_TEMPLATE_VERSION.encode()).hexdigest()
    seed_sha = _sha256_file(root / "evaluation" / "seeds" / "m0_topics.json")
    api_attempts = sum(result.request.attempts for result in snapshot.source_results)
    elapsed = max(0.0, (completed_at - started_at).total_seconds())
    budget = Budget(
        limits=BudgetLimits(max_duration_seconds=1800),
        usage=BudgetUsage(
            queries=len(snapshot.source_results),
            candidate_papers=len(snapshot.ranked_papers),
            api_calls=api_attempts,
            external_cost_cny=0,
            elapsed_seconds=elapsed,
        ),
    )
    storage_uri = snapshot_path.relative_to(root).as_posix()
    return RunManifest(
        run_id=f"run:m1:{snapshot.candidate_set_sha256[:24]}",
        task_id="task:dev-adaptive-rag",
        started_at=started_at,
        completed_at=completed_at,
        retrieval_cutoff=completed_at.date(),
        service_baselines=[
            ServiceBaseline(
                service="scholartrace",
                version=__version__,
                git_commit=git_commit,
                contract_version="search-1.0",
                capability_id="m1-multi-source-search",
                worktree_dirty=worktree_dirty,
                source_tree_sha256=source_tree_sha256,
            )
        ],
        model_policy_ref=ArtifactRef(
            artifact_id="artifact:model-policy:m0:v1",
            artifact_type="tool_run",
            content_sha256=model_policy_sha,
            storage_uri="contracts/examples/m0_bundle.json#ModelRoutingPolicy",
            created_at=started_at,
        ),
        model_usage=[],
        prompt_set_sha256=prompt_sha,
        evaluation_seed_sha256=seed_sha,
        data_snapshot_refs=[
            ArtifactRef(
                artifact_id=f"artifact:{snapshot.snapshot_id}",
                artifact_type="search_snapshot",
                content_sha256=snapshot_file_sha256,
                storage_uri=storage_uri,
                created_at=snapshot.generated_at,
            )
        ],
        budget=budget,
        outcome=snapshot.outcome,
    )
