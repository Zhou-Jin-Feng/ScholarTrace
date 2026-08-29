"""Durable event replay helpers for Server-Sent Events consumers."""

from __future__ import annotations

import json

from scholartrace.workflow.models import PersistedEvent
from scholartrace.workflow.storage import RuntimeLedger


def encode_sse(event: PersistedEvent) -> str:
    data = json.dumps(event.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.event_id}\nevent: {event.kind}\ndata: {data}\n\n"


def replay_sse(
    ledger: RuntimeLedger,
    *,
    task_id: str,
    last_event_id: str | None = None,
) -> list[str]:
    return [
        encode_sse(event)
        for event in ledger.replay(task_id=task_id, last_event_id=last_event_id)
    ]
