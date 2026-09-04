"""Measure the bounded local control plane without model or provider calls."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import tempfile
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

from scholartrace.api.app import create_app

TERMINAL_STATUSES = {"completed", "degraded", "cancelled", "failed", "rejected"}


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


async def _wait_terminal(client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + 10
    while True:
        response = await client.get(f"/api/v1/research/tasks/{task_id}")
        response.raise_for_status()
        task: dict[str, Any] = response.json()
        if task["status"] in TERMINAL_STATUSES:
            return task
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("task did not reach a terminal state")
        await asyncio.sleep(0.01)


async def _profile(
    *, root: Path, temp_root: Path, concurrency: int, task_count: int
) -> dict[str, object]:
    app = create_app(
        root=root,
        data_dir=temp_root / f"c{concurrency}",
        queue_capacity=4,
        worker_count=1,
        completion_wait_seconds=0.001,
    )
    transport = httpx.ASGITransport(app=app)
    latencies: list[float] = []
    retry_count = 0
    initial_rejections = 0
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://load") as client:
            created: list[str] = []
            for index in range(task_count):
                response = await client.post(
                    "/api/v1/research/tasks",
                    headers={"Idempotency-Key": f"m10-load-{concurrency}-{index}"},
                    json={"question": f"Sanitized local control-plane load case {index}"},
                )
                response.raise_for_status()
                created.append(str(response.json()["task_id"]))

            semaphore = asyncio.Semaphore(concurrency)

            async def approve(task_id: str) -> tuple[str, int]:
                async with semaphore:
                    started = perf_counter()
                    response = await client.post(
                        f"/api/v1/research/tasks/{task_id}/approve",
                        json={"action": "approve", "reason": "M10-P1 synthetic load"},
                    )
                    latencies.append(perf_counter() - started)
                    return task_id, response.status_code

            outcomes = await asyncio.gather(*(approve(task_id) for task_id in created))
            rejected = [task_id for task_id, status_code in outcomes if status_code == 429]
            unexpected = [
                status_code for _, status_code in outcomes if status_code not in {200, 429}
            ]
            if unexpected:
                raise RuntimeError(f"unexpected approval statuses: {unexpected}")
            initial_rejections = len(rejected)

            for task_id in rejected:
                while True:
                    response = await client.post(
                        f"/api/v1/research/tasks/{task_id}/approve",
                        json={"action": "approve", "reason": "M10-P1 bounded retry"},
                    )
                    retry_count += 1
                    if response.status_code == 200:
                        break
                    if response.status_code != 429:
                        response.raise_for_status()
                    await asyncio.sleep(0.01)

            terminal = await asyncio.gather(
                *(_wait_terminal(client, task_id) for task_id in created)
            )
            replays = await asyncio.gather(
                *(client.get(f"/api/v1/research/tasks/{task_id}/events") for task_id in created)
            )
            ready = await client.get("/api/v1/health/ready")
            ready.raise_for_status()
            statuses = [str(item["status"]) for item in terminal]
            return {
                "concurrency": concurrency,
                "task_count": task_count,
                "initial_accepted": task_count - initial_rejections,
                "initial_backpressure_429": initial_rejections,
                "retry_attempts": retry_count,
                "final_completed": statuses.count("completed"),
                "final_failed": sum(status != "completed" for status in statuses),
                "terminal_sse_replay_count": sum(
                    response.status_code == 200 and "exports_ready" in response.text
                    for response in replays
                ),
                "approval_latency_ms": {
                    "mean": round(statistics.fmean(latencies) * 1000, 3),
                    "p50": round(_percentile(latencies, 0.50) * 1000, 3),
                    "p95": round(_percentile(latencies, 0.95) * 1000, 3),
                    "max": round(max(latencies) * 1000, 3),
                },
                "queue_after_run": ready.json()["queue"],
            }
    finally:
        app.state.m6_service.close()


async def _run(task_count: int) -> dict[str, object]:
    root = Path(__file__).resolve().parents[1]
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="m10-p1-load-", dir=root / "artifacts") as temp:
        temp_root = Path(temp)
        profiles = [
            await _profile(
                root=root,
                temp_root=temp_root,
                concurrency=concurrency,
                task_count=task_count,
            )
            for concurrency in (1, 2, 4, 8)
        ]
    return {
        "schema_version": "1.0",
        "passed": all(
            item["final_completed"] == task_count
            and item["terminal_sse_replay_count"] == task_count
            and item["final_failed"] == 0
            for item in profiles
        ),
        "runtime": {
            "processes": 1,
            "workers": 1,
            "queue_capacity": 4,
            "profile": "deterministic_delivery_demo",
        },
        "profiles": profiles,
        "limitations": [
            "In-process synthetic control-plane load; not a model, PDF acquisition, "
            "or GPU benchmark.",
            "Backpressure is expected at burst sizes above the configured local queue capacity.",
            "No external API, DocuMind, ScholarGraph, Ollama, or paid model call was made.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-count", type=int, default=12)
    args = parser.parse_args()
    if args.task_count < 8:
        raise ValueError("task count must be at least 8")
    report = asyncio.run(_run(args.task_count))
    output = (
        Path(__file__).resolve().parents[1] / "evaluation" / "reports" / "m10_p1_local_load.json"
    )
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
