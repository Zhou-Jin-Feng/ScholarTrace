"""Validate a reachable DocuMind revision; frozen replay is explicit and offline by default."""

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
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import ValidationError

from scholartrace.evidence.models import (
    DocuMindErrorEnvelope,
    DocuMindReadiness,
    DocuMindRetrieveRequest,
    DocuMindRetrieveResponse,
    retrieval_is_ready,
)
from scholartrace.search.storage import write_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROVIDER = ROOT.parent / "DocuMind"
DEFAULT_OUTPUT = ROOT / "artifacts" / "reports" / "m2_documind_compatibility.json"
BASELINES = (("2.1.0", "32c5eb8"), ("2.2.0", "212f60a"))
CONTRACT_PATH = "docs/contracts/retrieve-v1.schema.json"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return result.stdout.decode("utf-8")


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


def _current_baseline(provider: Path, ref: str) -> tuple[str, str]:
    if not ref or ref.startswith("-"):
        raise ValueError("invalid provider ref")
    commit = _git(provider, "rev-parse", "--verify", ref + "^{commit}").strip()
    if not re.fullmatch(r"[a-f0-9]{40}", commit):
        raise ValueError("invalid resolved provider commit")
    reachable = _git(provider, "for-each-ref", "--contains=" + commit, "--format=%(refname)")
    if not reachable.strip():
        raise ValueError("provider revision must be reachable from a branch or tag")
    version_text = _git(provider, "show", commit + ":app/__init__.py")
    match = re.search(r'__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', version_text)
    if match is None:
        raise ValueError("provider version unavailable")
    return match.group(1), commit


def _online_readiness(
    base_url: str, timeout_seconds: float, expected_versions: set[str]
) -> dict[str, object]:
    try:
        with httpx.Client(base_url=base_url, timeout=timeout_seconds, trust_env=False) as client:
            response = client.get("/api/v1/health/ready")
        readiness = DocuMindReadiness.model_validate_json(response.content)
        version = readiness.version
        retrieval = (readiness.components or {}).get("retrieval")
        compatible = version in expected_versions and retrieval_is_ready(
            readiness, response.status_code
        )
        return {
            "checked": True,
            "http_status": response.status_code,
            "service_version": version,
            "retrieval_component": retrieval,
            "compatible": compatible,
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {"checked": True, "compatible": False, "error_type": type(exc).__name__}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-repo", type=Path, default=DEFAULT_PROVIDER)
    parser.add_argument("--provider-ref", default="HEAD")
    parser.add_argument(
        "--historical",
        action="store_true",
        help="Replay the original 2.1.0/2.2.0 objects; requires a private archive.",
    )
    parser.add_argument(
        "--base-url",
        help="Opt in to readiness GET; Provider may probe embeddings, not generation.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=5)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if args.historical and args.provider_ref != "HEAD":
        parser.error("--provider-ref cannot be combined with --historical")
    selected: tuple[tuple[str, str], ...] = ()
    try:
        selected = (
            BASELINES
            if args.historical
            else (_current_baseline(args.provider_repo, args.provider_ref),)
        )
        baselines = [
            _validate_baseline(args.provider_repo, version, commit) for version, commit in selected
        ]
        online = (
            _online_readiness(args.base_url, args.timeout_seconds, {v for v, _ in selected})
            if args.base_url
            else {"checked": False, "compatible": None}
        )
        passed = all(bool(row["passed"]) for row in baselines) and (
            not args.base_url or online.get("compatible") is True
        )
        summary: dict[str, object] = {
            "schema_version": "1.0",
            "generated_at": datetime.now(UTC).isoformat(),
            "scope": "historical_contract_replay" if args.historical else "current_contract",
            "provider_worktree_dirty": bool(_git(args.provider_repo, "status", "--porcelain")),
            "baselines": baselines,
            "online_readiness": online,
            "passed": passed,
            "limits": [
                "Committed contract validation is not a live upload/retrieve or model test.",
                "Readiness does not authenticate the deployed Git revision.",
                "Historical reports are not overwritten by the default output.",
            ],
        }
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        SchemaError,
        SchemaValidationError,
        subprocess.CalledProcessError,
    ) as exc:
        summary = {
            "passed": False,
            "error_type": type(exc).__name__,
            "scope": "historical_contract_replay" if args.historical else "current_contract",
            "attempted_baselines": [
                {"version": version, "git_commit": commit} for version, commit in selected
            ],
            "hint": (
                "Consumer rejected the provider version or payload; compatibility needs review."
                if isinstance(exc, ValidationError)
                else "Check schema and reachable ref; historical replay needs archived objects."
            ),
        }
    write_json(args.output, summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
