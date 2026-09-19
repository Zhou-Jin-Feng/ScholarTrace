"""Explicit, bounded metadata probes. Construction and snapshots never use HTTP."""

from __future__ import annotations

import asyncio
import json
import math
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from scholartrace.delivery.authorization import RuntimePolicy
from scholartrace.evidence.models import DocuMindReadiness, retrieval_is_ready


class DependencyProbes:
    def __init__(
        self, policy: RuntimePolicy, *, ttl_seconds: float = 30, timeout_seconds: float = 2,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        for value, maximum in ((ttl_seconds, 300), (timeout_seconds, 10)):
            if isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError("probe timing must be finite and bounded")
        self.policy, self.ttl = policy, ttl_seconds
        self.timeout, self.clock = timeout_seconds, clock
        self._lock = threading.RLock()
        self._results: dict[str, tuple[float, dict[str, Any]]] = {}
        self._refreshing = False

    def snapshot(self) -> dict[str, dict[str, Any]]:
        configured = {"documind": self.policy.documind_url is not None,
                      "local_model": self.policy.local is not None, "api_strong": True,
                      "search_provider": bool(self.policy.allowed_search_providers)}
        with self._lock:
            result = {}
            for name, present in configured.items():
                cached = self._results.get(name)
                if cached is not None and self.clock() - cached[0] < self.ttl:
                    result[name] = dict(cached[1])
                else:
                    result[name] = {
                        "configured": present, "state": "unknown" if present else "unavailable",
                        "reason": "Metadata probe absent or expired." if present
                        else "Dependency is not configured.",
                        "remediation": "Run an explicit dependency check.",
                        "approved": False, "probe_scope": "metadata_only",
                    }
            return result

    async def refresh(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
        with self._lock:
            if self._refreshing:
                return self.snapshot()
            self._refreshing = True
        try:
            targets = {"api_strong": str(httpx.URL(self.policy.remote.endpoint).copy_with(
                path="/v1/models"))}
            if self.policy.documind_url:
                targets["documind"] = self.policy.documind_url.rstrip("/") + "/api/v1/health/ready"
            if self.policy.local:
                targets["local_model"] = str(httpx.URL(self.policy.local.endpoint).copy_with(
                    path="/api/tags"))
            for name, url in targets.items():
                with self._lock:
                    cached = self._results.get(name)
                    if cached is not None and self.clock() - cached[0] < self.ttl:
                        continue
                state, reason = await self._probe(client, name, url)
                with self._lock:
                    self._results[name] = (self.clock(), {
                        "configured": True, "state": state, "reason": reason,
                        "remediation": None if state == "ready" else "Check configuration.",
                        "approved": False, "probe_scope": "metadata_only",
                    })
            return self.snapshot()
        finally:
            with self._lock:
                self._refreshing = False

    async def _probe(self, client: httpx.AsyncClient, name: str, url: str) -> tuple[str, str]:
        try:
            async with asyncio.timeout(self.timeout):
                async with client.stream("GET", url, follow_redirects=False,
                                         timeout=self.timeout) as response:
                    if response.status_code in {401, 403}:
                        return "unavailable", "Metadata access was denied; no retry was made."
                    allowed = {200, 503} if name == "documind" else {200}
                    if response.status_code not in allowed:
                        return "unavailable", "Metadata endpoint did not report an accepted status."
                    raw = bytearray()
                    async for part in response.aiter_bytes():
                        raw.extend(part)
                        if len(raw) > 262_144:
                            return "error", "Metadata response exceeded its size limit."
                    body = json.loads(raw)
                    ready = False
                    if name == "documind":
                        health = DocuMindReadiness.model_validate(body)
                        ready = (health.version == "3.0.0"
                                 and retrieval_is_ready(health, response.status_code))
                    elif isinstance(body, dict):
                        if name == "local_model" and self.policy.local is not None:
                            items = body.get("models")
                            ready = isinstance(items, list) and any(
                                isinstance(item, dict) and
                                item.get("name", item.get("model")) == self.policy.local.model
                                for item in items
                            )
                        elif name == "api_strong":
                            items = body.get("data")
                            ready = isinstance(items, list) and any(
                                isinstance(item, dict)
                                and item.get("id") == self.policy.remote.model
                                for item in items
                            )
                    if ready:
                        return "ready", "Metadata check passed; task authorization is separate."
                    return "unavailable", "Required version, model or component is absent."
        except (TimeoutError, httpx.HTTPError, ValueError):
            return "error", "Metadata check failed or timed out; no retry was made."
