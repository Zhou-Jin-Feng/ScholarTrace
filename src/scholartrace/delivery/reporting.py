"""Safe Markdown, HTML, and minimal PDF report renderers."""

from __future__ import annotations

import html
import re
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
    """Build a dependency-free, text-only PDF for reliable local export."""

    lines = [re.sub(r"[`*_]", "", line)[:110] for line in markdown.splitlines()]
    lines = lines[:48]
    text_commands = ["BT", "/F1 10 Tf", "50 760 Td"]
    for index, line in enumerate(lines):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if index:
            text_commands.append("0 -15 Td")
        text_commands.append(f"({escaped}) Tj")
    text_commands.append("ET")
    stream = "\n".join(text_commands).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        (
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        ),
    ]
    result = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode("ascii"))
        result.extend(obj)
        result.extend(b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    result.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    result.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(result)
