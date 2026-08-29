"""Deterministic M2 Markdown rendering over validated paper evidence bundles."""

from __future__ import annotations

import hashlib
from datetime import datetime

from scholartrace.evidence.models import EvidenceReportArtifact, PaperAnalysisBundle


def build_evidence_report(
    *,
    question: str,
    analyses: list[PaperAnalysisBundle],
    generated_at: datetime,
) -> EvidenceReportArtifact:
    ordered = sorted(analyses, key=lambda item: item.paper_card.canonical_paper_id)
    lines = [
        "# Evidence-Grounded Research Draft",
        "",
        f"Question: {question}",
        "",
        "> M2 draft: claims are extractive and provenance-checked, but independent semantic "
        "verification is deferred to M4.",
        "",
    ]
    for analysis in ordered:
        card = analysis.paper_card
        evidence_by_id = {item.evidence_id: item for item in analysis.evidence}
        lines.extend(
            [
                f"## {card.title} ({card.publication_year})",
                "",
                f"Paper ID: `{card.canonical_paper_id}`",
                "",
                card.summary,
                "",
                "### Contributions",
                "",
                *(f"- {item}" for item in card.contributions),
                "",
                "### Evidence-Linked Claims",
                "",
            ]
        )
        for claim in analysis.claims:
            lines.extend((f"- {claim.text}",))
            for evidence_id in claim.evidence_ids:
                evidence = evidence_by_id[evidence_id]
                page = str(evidence.page_number) if evidence.page_number is not None else "n/a"
                lines.extend(
                    (
                        f"  - Citation: `{evidence.canonical_paper_id}`; page {page}; "
                        f"chunk `{evidence.chunk_id}`; level `{evidence.evidence_level}`",
                        f'  - Quote: "{evidence.quote}"',
                    )
                )
        lines.extend(
            [
                "",
                "### Limitations",
                "",
                *(f"- {item}" for item in card.limitations),
                "",
            ]
        )
    limitations = [
        "Only the retrieved DocuMind chunks were available to the local model.",
        "M2 validates identity, location, hashes, and exact quotes but not semantic entailment.",
        "No cross-paper synthesis or unsupported conflict resolution is performed.",
    ]
    lines.extend(("## Report Limitations", "", *(f"- {item}" for item in limitations), ""))
    content = "\n".join(lines)
    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    report_digest = hashlib.sha256(
        "\0".join(
            (
                question,
                content_sha256,
                *(item.paper_card.canonical_paper_id for item in ordered),
            )
        ).encode("utf-8")
    ).hexdigest()
    return EvidenceReportArtifact(
        report_id=f"report:m2:{report_digest[:24]}",
        question=question,
        analyses=ordered,
        content_markdown=content,
        content_sha256=content_sha256,
        limitations=limitations,
        generated_at=generated_at,
    )
