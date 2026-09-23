"""Small, strict parser for OpenAI-compatible Responses SSE envelopes."""

from __future__ import annotations

import json
from typing import Any

import httpx

from .plan_generator import ProviderInferenceError


class ProviderStreamError(ProviderInferenceError):
    """The provider stream ended without a usable Responses envelope."""


async def collect_responses_sse(
    response: httpx.Response,
    *,
    model: str,
    max_response_bytes: int,
) -> bytes:
    """Collect a Responses SSE stream and normalize it to the non-stream contract."""

    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" not in content_type:
        body = await response.aread()
        if len(body) > max_response_bytes:
            raise ProviderStreamError("provider stream response exceeded size limit")
        return body

    total_bytes = 0
    event_name: str | None = None
    data_lines: list[str] = []
    text_deltas: list[str] = []
    final_envelope: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    response_model: str | None = None

    def consume_event(name: str | None, raw_data: str) -> None:
        nonlocal final_envelope, usage, response_model
        if raw_data == "[DONE]":
            return
        try:
            event = json.loads(raw_data)
        except json.JSONDecodeError as exc:
            raise ProviderStreamError(
                "provider stream contained invalid JSON",
                diagnostics={"category": "stream_json"},
            ) from exc
        if not isinstance(event, dict):
            raise ProviderStreamError(
                "provider stream event was not an object",
                diagnostics={"category": "stream_json"},
            )
        event_type = str(event.get("type") or name or "")
        if event_type in {"error", "response.failed", "response.incomplete"}:
            raise ProviderStreamError(
                "provider Responses stream reported an error",
                diagnostics={"category": "stream_error", "response_status": event_type},
            )
        delta = event.get("delta")
        if event_type == "response.output_text.delta" and isinstance(delta, str):
            text_deltas.append(delta)
        if event_type == "response.output_text.done" and not text_deltas:
            text = event.get("text")
            if isinstance(text, str):
                text_deltas.append(text)
        event_usage = event.get("usage")
        if isinstance(event_usage, dict):
            usage = event_usage
        nested = event.get("response")
        if isinstance(nested, dict):
            final_envelope = nested
            if isinstance(nested.get("usage"), dict):
                usage = nested["usage"]
            if isinstance(nested.get("model"), str):
                response_model = nested["model"]
        elif all(key in event for key in ("status", "output")):
            final_envelope = event
        if isinstance(event.get("model"), str):
            response_model = event["model"]

    async for line in response.aiter_lines():
        total_bytes += len(line.encode("utf-8")) + 1
        if total_bytes > max_response_bytes:
            raise ProviderStreamError("provider stream response exceeded size limit")
        if line.startswith("event:"):
            event_name = line[6:].strip() or None
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif not line and data_lines:
            consume_event(event_name, "\n".join(data_lines))
            event_name = None
            data_lines = []
    if data_lines:
        consume_event(event_name, "\n".join(data_lines))

    if final_envelope is None:
        final_envelope = {}
    if "status" not in final_envelope:
        final_envelope["status"] = "completed"
    if "model" not in final_envelope:
        final_envelope["model"] = response_model or model
    if "usage" not in final_envelope and usage is not None:
        final_envelope["usage"] = usage
    if "output" not in final_envelope and text_deltas:
        final_envelope["output"] = [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "".join(text_deltas)}
                ],
            }
        ]
    if not final_envelope.get("output") or not isinstance(final_envelope.get("usage"), dict):
        raise ProviderStreamError(
            "provider Responses stream did not contain a complete response",
            diagnostics={
                "category": "incomplete_response",
                "response_status": final_envelope.get("status"),
                "usage_present": isinstance(final_envelope.get("usage"), dict),
            },
        )
    return json.dumps(
        final_envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
