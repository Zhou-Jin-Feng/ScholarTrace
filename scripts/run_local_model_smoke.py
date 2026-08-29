"""Run fixed M0 structured-output cases against a local Ollama model."""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "evaluation" / "seeds" / "model_comparison_cases.json"
DEFAULT_OUTPUT = ROOT / "evaluation" / "reports" / "m0_local_model_smoke.json"


def request_case(
    *, base_url: str, model: str, case: dict[str, Any], timeout_seconds: float
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "messages": [
            {"role": "system", "content": case["system"]},
            {"role": "user", "content": case["input"]},
        ],
        "format": case["output_schema"],
        "options": {"temperature": 0, "num_ctx": 4096},
        "keep_alive": "0s",
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        result = json.loads(response.read().decode("utf-8"))
    elapsed = time.perf_counter() - started
    parsed = json.loads(result["message"]["content"])
    metrics = {
        "elapsed_seconds": round(elapsed, 3),
        "total_duration_seconds": round(result.get("total_duration", 0) / 1e9, 3),
        "load_duration_seconds": round(result.get("load_duration", 0) / 1e9, 3),
        "prompt_eval_count": int(result.get("prompt_eval_count", 0)),
        "eval_count": int(result.get("eval_count", 0)),
    }
    return parsed, metrics


def semantic_rule_passed(case: dict[str, Any], parsed: dict[str, Any]) -> bool:
    rule = case["semantic_rule"]
    if rule == "three_nonempty_queries":
        queries = parsed.get("queries")
        return isinstance(queries, list) and len(queries) == 3 and all(queries)
    if rule == "exact_evidence_binding":
        return (
            parsed.get("evidence_id") == "evidence:crag:abstract:01"
            and parsed.get("support") == "supported"
        )
    if rule == "relevant_true":
        return parsed.get("relevant") is True
    raise ValueError(f"unknown semantic rule: {rule}")


def percentile_95(values: list[float]) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=20, method="inclusive")[18]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()

    suite = json.loads(args.cases.read_text("utf-8"))
    case_results: list[dict[str, Any]] = []
    for case in suite["cases"]:
        try:
            parsed, metrics = request_case(
                base_url=args.base_url,
                model=args.model,
                case=case,
                timeout_seconds=args.timeout_seconds,
            )
            semantic_pass = semantic_rule_passed(case, parsed)
            case_results.append(
                {
                    "case_id": case["case_id"],
                    "node": case["node"],
                    "structured_success": True,
                    "semantic_rule_passed": semantic_pass,
                    "metrics": metrics,
                    "error": None,
                }
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError, urllib.error.URLError) as exc:
            case_results.append(
                {
                    "case_id": case["case_id"],
                    "node": case["node"],
                    "structured_success": False,
                    "semantic_rule_passed": False,
                    "metrics": {},
                    "error": type(exc).__name__,
                }
            )

    total = len(case_results)
    latencies = [
        result["metrics"]["elapsed_seconds"] for result in case_results if result["metrics"]
    ]
    summary = {
        "structured_success_rate": sum(r["structured_success"] for r in case_results) / total,
        "semantic_rule_pass_rate": sum(r["semantic_rule_passed"] for r in case_results) / total,
        "unsupported_critical_claim_rate": 0.0
        if all(r["semantic_rule_passed"] for r in case_results)
        else None,
        "p95_latency_seconds": round(percentile_95(latencies), 3) if latencies else None,
        "api_cost_cny": 0.0,
        "gpu_time_status": "not_measured_separately_from_wall_time",
    }
    thresholds = suite["thresholds"]
    passed = (
        summary["structured_success_rate"] >= thresholds["structured_success_rate"]
        and summary["semantic_rule_pass_rate"] >= thresholds["semantic_rule_pass_rate"]
        and summary["p95_latency_seconds"] is not None
        and summary["p95_latency_seconds"] <= thresholds["max_p95_latency_seconds"]
    )
    report = {
        "schema_version": "1.0",
        "suite_id": suite["suite_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "profile": {
            "provider": "ollama",
            "model": args.model,
            "model_id": "500a1f067a9f",
            "quantization": "Q4_K_M",
            "context_window": 40960,
            "request_context_window": 4096,
        },
        "summary": summary,
        "thresholds": thresholds,
        "cases": case_results,
        "passed": passed,
        "notes": [
            "No raw model responses are stored.",
            "This is a three-case local smoke, not a provider capacity or quality benchmark.",
            "GPU time requires separate instrumentation in later benchmark runs.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"passed": passed, "summary": summary}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
