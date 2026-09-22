"""Minimal no-report compatibility probe for the current cc-switch Luna provider."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sqlite3
import time
import tomllib
from pathlib import Path
from typing import Any

import httpx

from scholartrace.search.storage import write_json

ROOT = Path(__file__).resolve().parents[1]
CCSWITCH_DB = Path.home() / ".cc-switch" / "cc-switch.db"
OUTPUT = ROOT / "evaluation" / "reports" / "sa_04_current_luna_compatibility_probe.json"
PROVIDER_ID = "sub2api-1789904127847"
MODEL = "gpt-5.6-luna"


def _settings() -> tuple[str, str]:
    con = sqlite3.connect(CCSWITCH_DB)
    try:
        row = con.execute(
            "select notes, settings_config from providers where id=? and app_type='codex'",
            (PROVIDER_ID,),
        ).fetchone()
    finally:
        con.close()
    if row is None or row[0] != "Luna":
        raise ValueError("current cc-switch Luna provider was not found")
    payload = json.loads(row[1])
    config = tomllib.loads(payload["config"])
    base_url = config["model_providers"]["custom"]["base_url"].rstrip("/")
    api_key = payload.get("auth", {}).get("OPENAI_API_KEY", "")
    if not api_key.strip():
        raise ValueError("cc-switch Luna API key is empty")
    return base_url, api_key


def _safe_usage(body: dict[str, Any]) -> dict[str, Any] | None:
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    result: dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = value
    return result or None


async def _probe(
    *,
    client: httpx.AsyncClient,
    url: str,
    api_key: str,
    name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = await client.post(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": f"sa04-probe-{name}-luna-max",
            },
            json=payload,
            timeout=180,
        )
        body = await response.aread()
        body_hash = hashlib.sha256(body).hexdigest()
        parsed: dict[str, Any] | None = None
        try:
            candidate = json.loads(body)
            if isinstance(candidate, dict):
                parsed = candidate
        except json.JSONDecodeError:
            parsed = None
        return {
            "name": name,
            "status_code": response.status_code,
            "duration_seconds": round(time.perf_counter() - started, 6),
            "response_bytes": len(body),
            "response_sha256": body_hash,
            "response_model": parsed.get("model") if parsed else None,
            "response_status": parsed.get("status") if parsed else None,
            "usage": _safe_usage(parsed) if parsed else None,
            "response_json": parsed is not None,
        }
    except httpx.TimeoutException:
        return {
            "name": name,
            "error_category": "timeout",
            "duration_seconds": round(time.perf_counter() - started, 6),
        }
    except httpx.HTTPError as exc:
        return {
            "name": name,
            "error_category": type(exc).__name__,
            "duration_seconds": round(time.perf_counter() - started, 6),
        }


async def run() -> dict[str, Any]:
    base_url, api_key = _settings()
    url = f"{base_url}/responses" if base_url.endswith("/v1") else f"{base_url}/v1/responses"
    common = {
        "model": MODEL,
        "max_output_tokens": 64,
        "reasoning": {"effort": "max"},
    }
    plain = {
        **common,
        "input": [
            {"role": "system", "content": "Return only the word OK."},
            {"role": "user", "content": "OK"},
        ],
    }
    strict = {
        **common,
        "input": [
            {"role": "system", "content": "Return a JSON object with ok=true."},
            {"role": "user", "content": "Probe strict structured output."},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "sa04_probe",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
            }
        },
    }
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        results = [
            await _probe(client=client, url=url, api_key=api_key, name="plain", payload=plain),
            await _probe(
                client=client,
                url=url,
                api_key=api_key,
                name="strict_json_schema",
                payload=strict,
            ),
        ]
    return {
        "schema_version": "1.0",
        "purpose": "scholartrace-sa04-current-luna-responses-compatibility-probe",
        "provider_source": "cc-switch",
        "provider_id": PROVIDER_ID,
        "provider_hostname": base_url.split("/")[2],
        "model": MODEL,
        "reasoning_effort": "max",
        "endpoint_path": "/v1/responses",
        "results": results,
        "api_key_stored_publicly": False,
        "raw_response_stored_publicly": False,
        "calls_are_not_sa04_pilot_results": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    payload = asyncio.run(run())
    write_json(args.output, payload)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "results": [
                    {
                        "name": item["name"],
                        "status_code": item.get("status_code"),
                        "error_category": item.get("error_category"),
                        "duration_seconds": item.get("duration_seconds"),
                    }
                    for item in payload["results"]
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
