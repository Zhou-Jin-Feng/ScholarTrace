"""Safe Markdown, HTML, and paginated PDF report renderers."""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence


def render_markdown(
    *,
    task: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
    artifacts: Sequence[Mapping[str, object]],
) -> str:
    status = str(task["status"])
    phase = str(task["phase"])
    lines = [
        "# ScholarTrace Research Task Report",
        "",
        f"- Task: `{task['task_id']}`",
        f"- Title: {task['title']}",
        f"- Status: `{status}`",
        f"- Phase: `{phase}`",
        "",
        "## Question",
        "",
        str(task["question"]),
        "",
        "## Timeline",
        "",
    ]
    if events:
        for event in events:
            lines.append(
                f"- `{event['event_id']}` `{event['kind']}` ({event['node']})"
            )
    else:
        lines.append("- No persisted events.")
    lines.extend(("", "## Runtime Summary", ""))
    metrics = task.get("metrics")
    if isinstance(metrics, Mapping) and metrics:
        lines.extend(f"- {key}: `{value}`" for key, value in sorted(metrics.items()))
    else:
        lines.append("- No runtime metrics recorded.")
    degradations = task.get("degradations")
    if isinstance(degradations, Sequence) and not isinstance(degradations, (str, bytes)):
        lines.extend(("", "## Degradations", ""))
        if degradations:
            lines.extend(f"- {item}" for item in degradations)
        else:
            lines.append("- None.")
    lines.extend(("", "## Artifacts", ""))
    if artifacts:
        lines.extend(
            f"- `{item['artifact_id']}` {item['artifact_type']} "
            f"({item['size_bytes']} bytes, `{item['content_sha256']}`)"
            for item in artifacts
        )
    else:
        lines.append("- None.")
    lines.extend(
        (
            "",
            "## Safety Notes",
            "",
            "- This report contains public task metadata and artifact hashes only.",
            "- It does not include API keys, raw model answers, prompts, or paper full text.",
            "",
        )
    )
    return "\n".join(lines)


def render_html(
    *,
    task: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
    artifacts: Sequence[Mapping[str, object]],
) -> str:
    markdown = render_markdown(task=task, events=events, artifacts=artifacts)
    paragraphs = []
    for line in markdown.splitlines():
        if line.startswith("# "):
            paragraphs.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            paragraphs.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("- "):
            paragraphs.append(f"<li>{html.escape(line[2:])}</li>")
        elif line.strip():
            paragraphs.append(f"<p>{html.escape(line)}</p>")
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>ScholarTrace Report</title>"
        "<style>body{font:16px system-ui,sans-serif;max-width:900px;margin:40px auto;"
        "padding:0 20px;color:#17202a}h1{color:#123b52}h2{margin-top:28px;color:#23636b}"
        "li{margin:6px 0}code{background:#eef3f4;padding:2px 4px}</style></head><body>"
        + "".join(paragraphs)
        + "</body></html>"
    )


def render_pdf(*, markdown: str) -> bytes:
    """Export every line with CJK text, wrapping and automatic pagination."""
    from scholartrace.delivery.pdf import build_pdf

    return build_pdf(markdown)
