from __future__ import annotations

import hashlib

from scholartrace.delivery.reporting import render_html, render_markdown, render_pdf


def _inputs() -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    return (
        {
            "task_id": "task:m6:test",
            "title": "Test task",
            "question": "How does retrieval work?",
            "status": "completed",
            "phase": "done",
            "metrics": {"model_calls": 0},
            "degradations": [],
        },
        [{"event_id": "event:1", "kind": "workflow_finished", "node": "delivery"}],
        [
            {
                "artifact_id": "artifact:m6:test:report:markdown",
                "artifact_type": "report",
                "content_sha256": "a" * 64,
                "size_bytes": 12,
            }
        ],
    )


def test_report_renderers_are_safe_and_deterministic() -> None:
    task, events, artifacts = _inputs()
    markdown = render_markdown(task=task, events=events, artifacts=artifacts)
    html = render_html(task=task, events=events, artifacts=artifacts)
    pdf = render_pdf(markdown=markdown)

    assert "raw model answers" in markdown
    assert "<h1>ScholarTrace Research Task Report</h1>" in html
    assert html.startswith("<!doctype html>")
    assert pdf.startswith(b"%PDF-1.4")
    assert hashlib.sha256(pdf).hexdigest() == hashlib.sha256(
        render_pdf(markdown=markdown)
    ).hexdigest()
