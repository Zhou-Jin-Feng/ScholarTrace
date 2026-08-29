"""Budget-checked atomic persistence for an M2 evidence run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
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
from scholartrace.evidence.analysis import (
    PAPER_ANALYSIS_PROMPT_VERSION,
    PAPER_ANALYSIS_SYSTEM_PROMPT,
)
from scholartrace.evidence.pipeline import EvidencePipelineResult
from scholartrace.search.storage import write_json, write_model, write_text


class BudgetExceededError(RuntimeError):
    """An M2 result exceeds the frozen M0 task budget."""


@dataclass(frozen=True, slots=True)
class PersistedM2Artifacts:
    manifest: RunManifest
    manifest_sha256: str
    report_json_sha256: str
    report_markdown_sha256: str
    retrieval_audits_sha256: str
    model_usage_sha256: str


def persist_m2_artifacts(
    *,
    root: Path,
    output_dir: Path,
    result: EvidencePipelineResult,
    git_commit: str,
    worktree_dirty: bool,
    source_tree_sha256: str,
    documind_version: str = "2.2.0",
    documind_commit: str = "212f60a",
    capability_id: str = "single-document-dense-retrieval-contract-fixture",
) -> PersistedM2Artifacts:
    budget = _build_budget(root=root, result=result)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_json_path = output_dir / "evidence_report.json"
    report_markdown_path = output_dir / "evidence_report.md"
    audits_path = output_dir / "retrieval_audits.json"
    usage_path = output_dir / "model_usage.json"

    report_json_sha = write_model(report_json_path, result.report)
    report_markdown_sha = write_text(report_markdown_path, result.report.content_markdown)
    audits_sha = write_json(
        audits_path,
        [item.model_dump(mode="json") for item in result.retrieval_audits],
    )
    usage_sha = write_json(
        usage_path,
        [item.model_dump(mode="json") for item in result.model_usage],
    )
    manifest = _build_manifest(
        root=root,
        result=result,
        budget=budget,
        git_commit=git_commit,
        worktree_dirty=worktree_dirty,
        source_tree_sha256=source_tree_sha256,
        documind_version=documind_version,
        documind_commit=documind_commit,
        capability_id=capability_id,
        report_json_path=report_json_path,
        report_json_sha=report_json_sha,
        report_markdown_path=report_markdown_path,
        report_markdown_sha=report_markdown_sha,
        audits_path=audits_path,
        audits_sha=audits_sha,
        usage_path=usage_path,
        usage_sha=usage_sha,
    )
    manifest_sha = write_model(output_dir / "run_manifest.json", manifest)
    return PersistedM2Artifacts(
        manifest=manifest,
        manifest_sha256=manifest_sha,
        report_json_sha256=report_json_sha,
        report_markdown_sha256=report_markdown_sha,
        retrieval_audits_sha256=audits_sha,
        model_usage_sha256=usage_sha,
    )


def _build_budget(*, root: Path, result: EvidencePipelineResult) -> Budget:
    limits = BudgetLimits.model_validate(
        json.loads((root / "contracts" / "examples" / "m0_bundle.json").read_text("utf-8"))[
            "Budget"
        ]["limits"]
    )
    input_tokens = sum(item.input_tokens for item in result.model_usage)
    output_tokens = sum(item.output_tokens for item in result.model_usage)
    rag_calls = sum(item.attempts for item in result.retrieval_audits)
    model_calls = sum(item.call_count for item in result.model_usage)
    elapsed = max(0.0, (result.completed_at - result.started_at).total_seconds())
    usage = BudgetUsage(
        queries=len(result.retrieval_audits),
        candidate_papers=len(result.report.analyses),
        fulltext_papers=len(result.report.analyses),
        rag_calls=rag_calls,
        llm_input_tokens=input_tokens,
        llm_output_tokens=output_tokens,
        api_calls=rag_calls,
        model_calls=model_calls,
        external_cost_cny=sum(item.billed_cost_cny for item in result.model_usage),
        local_gpu_seconds=sum(item.local_gpu_seconds for item in result.model_usage),
        elapsed_seconds=elapsed,
    )
    violations: list[str] = []
    checks = (
        (usage.queries, limits.max_queries, "queries"),
        (usage.candidate_papers, limits.max_candidate_papers, "candidate_papers"),
        (usage.fulltext_papers, limits.max_fulltext_papers, "fulltext_papers"),
        (usage.llm_input_tokens, limits.max_llm_input_tokens, "llm_input_tokens"),
        (usage.llm_output_tokens, limits.max_llm_output_tokens, "llm_output_tokens"),
        (usage.api_calls, limits.max_api_calls, "api_calls"),
        (usage.model_calls, limits.max_model_calls, "model_calls"),
        (usage.external_cost_cny, limits.max_cost_cny, "external_cost_cny"),
        (usage.elapsed_seconds, limits.max_duration_seconds, "elapsed_seconds"),
    )
    violations.extend(name for actual, maximum, name in checks if actual > maximum)
    if (
        limits.max_total_tokens is not None
        and usage.llm_input_tokens + usage.llm_output_tokens > limits.max_total_tokens
    ):
        violations.append("total_tokens")
    if any(item.attempts > limits.max_rag_calls_per_paper for item in result.retrieval_audits):
        violations.append("rag_calls_per_paper")
    if violations:
        raise BudgetExceededError("M2 budget exceeded: " + ", ".join(violations))
    return Budget(limits=limits, usage=usage)


def _build_manifest(
    *,
    root: Path,
    result: EvidencePipelineResult,
    budget: Budget,
    git_commit: str,
    worktree_dirty: bool,
    source_tree_sha256: str,
    documind_version: str,
    documind_commit: str,
    capability_id: str,
    report_json_path: Path,
    report_json_sha: str,
    report_markdown_path: Path,
    report_markdown_sha: str,
    audits_path: Path,
    audits_sha: str,
    usage_path: Path,
    usage_sha: str,
) -> RunManifest:
    model_policy_payload = json.loads(
        (root / "contracts" / "examples" / "m0_bundle.json").read_text("utf-8")
    )["ModelRoutingPolicy"]
    model_policy_sha = _canonical_sha256(model_policy_payload)
    prompt_sha = hashlib.sha256(
        f"{PAPER_ANALYSIS_PROMPT_VERSION}\0{PAPER_ANALYSIS_SYSTEM_PROMPT}".encode()
    ).hexdigest()
    seed_sha = hashlib.sha256(
        (root / "evaluation" / "seeds" / "m0_topics.json").read_bytes()
    ).hexdigest()
    created_at = result.completed_at
    refs = [
        ArtifactRef(
            artifact_id=f"artifact:{result.report.report_id}:json",
            artifact_type="report",
            content_sha256=report_json_sha,
            storage_uri=_storage_uri(root, report_json_path),
            created_at=created_at,
        ),
        ArtifactRef(
            artifact_id=f"artifact:{result.report.report_id}:markdown",
            artifact_type="report",
            content_sha256=report_markdown_sha,
            storage_uri=_storage_uri(root, report_markdown_path),
            created_at=created_at,
        ),
        ArtifactRef(
            artifact_id=f"artifact:{result.report.report_id}:retrieval-audits",
            artifact_type="tool_run",
            content_sha256=audits_sha,
            storage_uri=_storage_uri(root, audits_path),
            created_at=created_at,
        ),
        ArtifactRef(
            artifact_id=f"artifact:{result.report.report_id}:model-usage",
            artifact_type="tool_run",
            content_sha256=usage_sha,
            storage_uri=_storage_uri(root, usage_path),
            created_at=created_at,
        ),
    ]
    return RunManifest(
        run_id=f"run:m2:{result.report.report_id.rsplit(':', maxsplit=1)[-1]}",
        task_id="task:dev-adaptive-rag",
        started_at=result.started_at,
        completed_at=result.completed_at,
        retrieval_cutoff=result.completed_at.date(),
        service_baselines=[
            ServiceBaseline(
                service="scholartrace",
                version=__version__,
                git_commit=git_commit,
                contract_version="evidence-1.0",
                capability_id="m2-documind-evidence-loop",
                worktree_dirty=worktree_dirty,
                source_tree_sha256=source_tree_sha256,
            ),
            ServiceBaseline(
                service="documind",
                version=documind_version,
                git_commit=documind_commit,
                contract_version="retrieve-1.0",
                capability_id=capability_id,
                worktree_dirty=False,
            ),
        ],
        model_policy_ref=ArtifactRef(
            artifact_id="artifact:model-policy:m0:v1",
            artifact_type="tool_run",
            content_sha256=model_policy_sha,
            storage_uri="contracts/examples/m0_bundle.json#ModelRoutingPolicy",
            created_at=result.started_at,
        ),
        model_usage=result.model_usage,
        prompt_set_sha256=prompt_sha,
        evaluation_seed_sha256=seed_sha,
        data_snapshot_refs=refs,
        budget=budget,
        outcome="succeeded",
    )


def _canonical_sha256(payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _storage_uri(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
