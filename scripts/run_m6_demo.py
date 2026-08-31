"""Run the local M6 delivery demo and write only sanitized metrics."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import httpx

from scholartrace.api.app import create_app


async def _run() -> dict[str, object]:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="m6-demo-", dir=root / "artifacts") as temp:
        app = create_app(root=root, data_dir=Path(temp))
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://demo") as client:
                created = await client.post(
                    "/api/v1/research/tasks",
                    headers={"Idempotency-Key": "m6-script-demo"},
                    json={
                        "question": "How can retrieval evidence improve RAG reports?",
                        "demo_mode": "success",
                    },
                )
                created.raise_for_status()
                task_id = created.json()["task_id"]
                completed = await client.post(
                    f"/api/v1/research/tasks/{task_id}/approve",
                    json={"action": "approve", "reason": "scripted demo"},
                )
                completed.raise_for_status()
                task = completed.json()
                artifacts = await client.get(f"/api/v1/research/tasks/{task_id}/artifacts")
                artifacts.raise_for_status()
                events = await client.get(f"/api/v1/research/tasks/{task_id}/events")
                events.raise_for_status()
                return {
                    "schema_version": "1.0",
                    "passed": task["status"] == "completed",
                    "status": task["status"],
                    "phase": task["phase"],
                    "event_count": task["event_count"],
                    "artifact_count": len(artifacts.json()),
                    "export_formats": ["json", "markdown", "html", "pdf"],
                    "sse_contains_terminal_event": "workflow_finished" in events.text,
                    "execution_mode": task["metrics"].get("execution_mode"),
                    "notes": [
                        "Deterministic local delivery demo; no model or external provider calls.",
                        "Task identifiers and report content are not stored in this summary.",
                    ],
                }
        finally:
            app.state.m6_service.close()


def main() -> None:
    report = asyncio.run(_run())
    output = Path(__file__).resolve().parents[1] / "evaluation" / "reports" / "m6_demo_smoke.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
