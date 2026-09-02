from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast

from scholartrace.scholargraph.real_miss import (
    M9_PARQUET_BUNDLE_SHA256,
    build_m9_b5_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _run_cli(arguments: list[str]) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "manage_m9_p0.py"), *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    assert isinstance(payload, dict)
    return cast(dict[str, object], payload)


def test_cli_capture_review_and_status_keep_fixture_private() -> None:
    agent_root = ROOT / "agent"
    agent_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="m9-cli-test-", dir=agent_root) as directory:
        private_dir = Path(directory)
        database = private_dir / "observations.sqlite"
        capture_input = private_dir / "capture.json"
        snapshot_input = private_dir / "snapshot.json"
        review_input = private_dir / "review.json"
        public_report = private_dir / "m9-status.json"
        question = "Which RAG technique supports graph retrieval?"

        snapshot = build_m9_b5_snapshot(
            question=question,
            status="succeeded",
            reason="matched",
            candidate_openalex_ids=["W1"],
            parquet_bundle_sha256=M9_PARQUET_BUNDLE_SHA256,
        )
        _write_json(snapshot_input, snapshot.model_dump(mode="json"))
        _write_json(
            capture_input,
            {
                "schema_version": "1.0",
                "source_event_id": "task:m9:cli-fixture-1",
                "sample_origin": "fixture",
                "question": question,
                "eligibility": "graph_eligible",
                "eligibility_reason": "within_frozen_corpus_scope",
                "stratum": "relation_bridge",
            },
        )
        capture_result = _run_cli(
            [
                "--database",
                str(database),
                "capture",
                "--input",
                str(capture_input),
                "--b5-snapshot",
                str(snapshot_input),
                "--public-report",
                str(public_report),
            ]
        )
        assert capture_result["record_id"] == "m9-p0-000001"

        _write_json(
            review_input,
            {
                "schema_version": "1.0",
                "record_id": "m9-p0-000001",
                "expected_revision": 0,
                "status": "confirmed",
                "gold_candidate_ids": ["W2"],
                "gold_in_frozen_corpus_ids": ["W2"],
                "gold_basis": [
                    {
                        "openalex_id": "W2",
                        "kind": "openalex_metadata",
                        "authority_sha256": "b" * 64,
                    }
                ],
                "graph_path_status": "path_missing",
                "root_cause": "G1_ALIAS",
                "notes": "private CLI reviewer note",
            },
        )
        review_result = _run_cli(
            [
                "--database",
                str(database),
                "review",
                "--input",
                str(review_input),
                "--public-report",
                str(public_report),
            ]
        )
        assert review_result["review_revision"] == 1

        status_result = _run_cli(["--database", str(database), "status"])
        assert status_result["decision"] == "COLLECT_MORE"
        counts = status_result["counts"]
        assert isinstance(counts, dict)
        assert counts["real_observations"] == 0
        assert counts["fixture_observations_excluded"] == 1

        serialized_report = public_report.read_text("utf-8")
        assert question not in serialized_report
        assert "private CLI reviewer note" not in serialized_report
        assert json.loads(serialized_report)["records"] == []
