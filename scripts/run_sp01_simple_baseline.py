"""Run the zero-cost SP-01 single-pass baseline against the pilot inputs."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from pathlib import Path

from scholartrace.search.storage import source_tree_sha256, write_json
from scholartrace.verification_ablation import (
    DeterministicFixtureAblationReportGenerator,
    SimpleBaselineManifest,
    SimpleBaselineRunner,
    load_frozen_inputs,
)
from scholartrace.verification_ablation.simple_baseline import write_artifacts

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "agent" / "verification-ablation" / "SA-02" / "frozen_inputs.json"
SA02_MANIFEST = ROOT / "evaluation" / "seeds" / "sa_02_verification_ablation_manifest.json"
MANIFEST_OUTPUT = ROOT / "evaluation" / "seeds" / "sp_01_simple_baseline_manifest.json"
PRIVATE_DIR = ROOT / "agent" / "verification-ablation" / "SP-01-simple-baseline"
PRIVATE_ARCHIVE = PRIVATE_DIR / "pilot_archive.json"
PUBLIC_OUTPUT = ROOT / "evaluation" / "reports" / "sp_01_simple_baseline_fixture.json"


def current_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    ).strip()


def make_manifest() -> SimpleBaselineManifest:
    frozen_manifest = json.loads(SA02_MANIFEST.read_text(encoding="utf-8"))
    frozen_inputs = load_frozen_inputs(INPUTS)
    pilot = {
        question_id: item
        for question_id, item in frozen_inputs.items()
        if item.split == "pilot"
    }
    return SimpleBaselineManifest(
        experiment_id="scholartrace-sp01-simple-baseline-v1",
        execution_mode="fixture",
        baseline_commit=current_commit(),
        source_tree_sha256=source_tree_sha256(ROOT),
        model_profile="fixture",
        model_identifier="fixture",
        provider_protocol="fixture",
        report_prompt_sha256=DeterministicFixtureAblationReportGenerator.prompt_template_sha256,
        report_schema_sha256=DeterministicFixtureAblationReportGenerator.report_schema_sha256,
        report_length_limit_chars=5_000,
        dataset_fingerprint_sha256=frozen_manifest["dataset_fingerprint_sha256"],
        question_input_sha256={
            question_id: item.input_sha256
            for question_id, item in sorted(pilot.items())
        },
        max_context_characters=40_000,
    )


async def run() -> dict[str, object]:
    frozen_inputs = load_frozen_inputs(INPUTS)
    pilot = {
        question_id: item
        for question_id, item in frozen_inputs.items()
        if item.split == "pilot"
    }
    manifest = make_manifest()
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json(MANIFEST_OUTPUT, manifest.model_dump(mode="json"))
    archive = await SimpleBaselineRunner(
        report_generator=DeterministicFixtureAblationReportGenerator()
    ).run(
        manifest=manifest,
        inputs=pilot,
        run_id="sp01-simple-baseline-fixture-20260922",
    )
    write_artifacts(
        archive=archive,
        private_path=PRIVATE_ARCHIVE,
        public_path=PUBLIC_OUTPUT,
    )
    return {
        "passed": all(row.status in {"succeeded", "degraded"} for row in archive.rows),
        "question_count": len(archive.rows),
        "report_calls": archive.usage.report_calls,
        "model_calls": archive.usage.model_calls,
        "provider_api_calls": archive.usage.provider_api_calls,
        "raw_reports_public": False,
        "manifest_sha256": archive.manifest_sha256,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    result = asyncio.run(run())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
