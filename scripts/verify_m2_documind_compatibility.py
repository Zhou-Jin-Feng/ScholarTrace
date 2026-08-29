"""Validate ScholarTrace against frozen DocuMind retrieval Provider contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from scholartrace.evidence.models import (
    DocuMindErrorEnvelope,
    DocuMindRetrieveRequest,
    DocuMindRetrieveResponse,
)
from scholartrace.search.storage import write_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROVIDER = ROOT.parent / "DocuMind"
DEFAULT_OUTPUT = ROOT / "evaluation" / "reports" / "m2_documind_compatibility.json"
BASELINES = (("2.1.0", "32c5eb8"), ("2.2.0", "212f60a"))
CONTRACT_PATH = "docs/contracts/retrieve-v1.schema.json"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def _valid_payloads(service_version: str) -> tuple[dict[str, object], dict[str, object]]:
    content = "A retrieved evidence chunk."
    request = DocuMindRetrieveRequest(
        query="What evidence supports the claim?",
        document_key="a" * 64,
        expected_index_id="b" * 64,
        top_k=3,
    ).model_dump(mode="json")
    response: dict[str, object] = {
        "schema_version": "1.0",
        "service_version": service_version,
        "retrieval_version": "dense-v1",
        "retrieval_mode": "dense",
        "document_key": "a" * 64,
        "index_id": "b" * 64,
        "source_sha256": "c" * 64,
        "chunks": [
            {
                "chunk_id": "d" * 64,
                "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "source": "paper.pdf",
                "page_number": 3,
                "distance": 0.42,
                "rank": 1,
            }
        ],
    }
    return request, response


def _validate_baseline(provider: Path, version: str, commit: str) -> dict[str, object]:
    raw = _git(provider, "show", f"{commit}:{CONTRACT_PATH}")
    contract = json.loads(raw)
    request, response = _valid_payloads(version)
    error = {
        "error": {
            "code": "stale_document_index",
            "message": "The expected index is no longer active.",
            "request_id": "consumer-contract-1234",
        }
    }
    for kind, payload in (("request", request), ("response", response), ("error", error)):
        Draft202012Validator(contract[kind]).validate(payload)
    DocuMindRetrieveRequest.model_validate(request)
    DocuMindRetrieveResponse.model_validate(response)
    DocuMindErrorEnvelope.model_validate(error)

    invalid_request = {**request, "documents": []}
    provider_rejected_unknown = bool(
        list(Draft202012Validator(contract["request"]).iter_errors(invalid_request))
    )
    try:
        DocuMindRetrieveRequest.model_validate(invalid_request)
    except ValidationError:
        consumer_rejected_unknown = True
    else:
        consumer_rejected_unknown = False
    passed = provider_rejected_unknown and consumer_rejected_unknown
    return {
        "version": version,
        "git_commit": _git(provider, "rev-parse", commit).strip(),
        "schema_version": contract["schema_version"],
        "contract_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "valid_request_response_error_passed": True,
        "unknown_field_rejected_by_both": passed,
        "passed": passed,
    }


def _online_readiness(base_url: str, timeout_seconds: float) -> dict[str, object]:
    try:
        with httpx.Client(base_url=base_url, timeout=timeout_seconds, trust_env=False) as client:
            response = client.get("/api/v1/health/ready")
        payload = response.json()
        version = payload.get("version") if isinstance(payload, dict) else None
        components = payload.get("components") if isinstance(payload, dict) else None
        retrieval = components.get("retrieval") if isinstance(components, dict) else None
        version_match = (
            re.fullmatch(r"2\.(\d+)\.\d+", version) if isinstance(version, str) else None
        )
        compatible = (
            version_match is not None
            and int(version_match.group(1)) >= 1
            and (retrieval == "ready" or (components is None and response.is_success))
        )
        return {
            "reachable": True,
            "http_status": response.status_code,
            "service_version": version,
            "retrieval_component": retrieval,
            "compatible": compatible,
        }
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        return {
            "reachable": False,
            "http_status": None,
            "service_version": None,
            "retrieval_component": None,
            "compatible": False,
            "error_type": type(exc).__name__,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-repo", type=Path, default=DEFAULT_PROVIDER)
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--timeout-seconds", type=float, default=5)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    baselines = [
        _validate_baseline(args.provider_repo, version, commit) for version, commit in BASELINES
    ]
    online = _online_readiness(args.base_url, args.timeout_seconds)
    passed = all(bool(item["passed"]) for item in baselines)
    summary: dict[str, object] = {
        "schema_version": "1.0",
        "generated_at": datetime.now(UTC).isoformat(),
        "consumer": "scholartrace-m2",
        "provider": "documind",
        "provider_worktree_dirty": bool(_git(args.provider_repo, "status", "--porcelain")),
        "frozen_baselines": baselines,
        "online_readiness": online,
        "online_acceptance_passed": bool(online["compatible"]),
        "passed": passed,
        "notes": [
            (
                "Frozen Provider commits were read with git show; the DocuMind worktree "
                "was not modified."
            ),
            (
                "Online readiness is reported separately and does not replace frozen "
                "contract validation."
            ),
            (
                "No response body, document identity, query text, Chunk content, or "
                "credential is stored."
            ),
        ],
    }
    write_json(args.output, summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
