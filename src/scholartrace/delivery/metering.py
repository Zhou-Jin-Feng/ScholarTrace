"""Explicitly configured model transport with durable pre-dispatch accounting.

No credentials or endpoint are discovered automatically. Composition must supply
the approved endpoint, model, prices and task ceiling. Stored usage is a reference
estimate, never a provider bill. This transport does not retry or follow redirects.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from scholartrace.delivery.authorization import AuthorizationError, CallContext, CallUsage
from scholartrace.delivery.budget import validate_budget_values
from scholartrace.delivery.effects import EffectJournal, EffectUncertainError, MeasuredUsageError

ContextSource = CallContext | Callable[[], CallContext]


def _resolve_context(source: ContextSource) -> CallContext:
    context = source() if callable(source) else source
    if not isinstance(context, CallContext):
        raise AuthorizationError("context provider did not return CallContext")
    return context


@dataclass(frozen=True)
class ModelCallPolicy:
    endpoint: str
    model: str
    input_cny_per_million: float
    output_cny_per_million: float
    max_input_tokens: int
    max_output_tokens: int
    max_cny: float
    max_calls: int
    model_version: str | None = None

    def __post_init__(self) -> None:
        url = httpx.URL(self.endpoint)
        if (
            url.scheme != "https"
            or url.userinfo
            or url.query
            or url.fragment
            or url.path not in {"/v1/chat/completions", "/v1/responses"}
        ):
            raise ValueError("model endpoint must be an explicit HTTPS inference route")
        if not self.model.strip():
            raise ValueError("approved model must not be blank")
        validate_budget_values(self.max_cny, self.max_calls)
        validate_budget_values(self.input_cny_per_million, self.max_input_tokens)
        validate_budget_values(self.output_cny_per_million, self.max_output_tokens)
        if min(self.max_input_tokens, self.max_output_tokens) < 1:
            raise ValueError("token limits must be positive")

    def cost(self, inputs: int, outputs: int) -> float:
        return float(
            (
                Decimal(str(self.input_cny_per_million)) * inputs
                + Decimal(str(self.output_cny_per_million)) * outputs
            )
            / 1_000_000
        )


class MeteredModelTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        *,
        inner: httpx.AsyncBaseTransport,
        journal: EffectJournal,
        task_id: str,
        policy: ModelCallPolicy,
        cancel_event: threading.Event,
        context: ContextSource | None = None,
    ) -> None:
        self.inner = inner
        self.journal = journal
        self.task_id = task_id
        self.policy = policy
        self.cancel_event = cancel_event
        self.context = context

    def _validate_authorized_policy(self, context: CallContext | None = None) -> None:
        context = context or (None if self.context is None else _resolve_context(self.context))
        if context is None:
            return
        assert context is not None
        if context.call_kind != "remote_model":
            raise AuthorizationError("model transport requires remote model context")
        snapshot = self.journal.approved_policy(self.task_id)
        if snapshot.digest() != context.policy_sha256:
            raise AuthorizationError("transport requires an approved task policy")
        approved = snapshot.remote
        policy = self.policy
        if (approved.endpoint != policy.endpoint or approved.model != policy.model
                or approved.model_version != policy.model_version
                or Decimal(approved.input_cny_per_million)
                != Decimal(str(policy.input_cny_per_million))
                or Decimal(approved.output_cny_per_million)
                != Decimal(str(policy.output_cny_per_million))
                or approved.max_input_tokens != policy.max_input_tokens
                or approved.max_output_tokens != policy.max_output_tokens):
            raise AuthorizationError("transport configuration differs from the approved policy")

    async def aclose(self) -> None:
        await self.inner.aclose()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        policy = self.policy
        context = None if self.context is None else _resolve_context(self.context)
        self._validate_authorized_policy(context)
        if request.method != "POST" or request.url != httpx.URL(policy.endpoint):
            raise ValueError("request is outside the approved model endpoint")
        content = await request.aread()
        body = json.loads(content)
        if not isinstance(body, dict) or body.get("model") != policy.model:
            raise ValueError("request model differs from the approved model")
        if body.get("stream"):
            raise ValueError("streamed provider metering is not enabled")
        limit = body.get("max_completion_tokens", body.get("max_output_tokens"))
        if type(limit) is not int or not 1 <= limit <= policy.max_output_tokens:
            raise ValueError("request must enforce the approved output-token ceiling")
        if len(content) > policy.max_input_tokens:
            raise ValueError("request exceeds the conservative input-size allowance")
        digest = hashlib.sha256(content).hexdigest()
        identity = {
            "endpoint": policy.endpoint,
            "model": policy.model,
            "body_sha256": digest,
            "policy": {
                "input_price": policy.input_cny_per_million,
                "output_price": policy.output_cny_per_million,
                "input_limit": policy.max_input_tokens,
                "output_limit": policy.max_output_tokens,
            },
        }
        key = "model:" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        request_context = context
        if request_context is not None:
            key = request_context.effect_key()

        async def dispatch() -> dict[str, Any]:
            response = await self.inner.handle_async_request(request)
            try:
                raw = bytearray()
                async for block in response.aiter_bytes():
                    raw.extend(block)
                    if len(raw) > 2_000_000:
                        raise EffectUncertainError("provider response exceeded its size limit")
                if response.status_code < 200 or response.status_code >= 300:
                    raise EffectUncertainError("provider response did not confirm successful usage")
                envelope = json.loads(raw)
                if not isinstance(envelope, dict) or envelope.get("model") != policy.model:
                    raise EffectUncertainError("provider returned an unapproved model")
                usage = envelope.get("usage")
                if not isinstance(usage, dict):
                    raise EffectUncertainError("provider usage is unknown")
                inputs = usage.get("prompt_tokens", usage.get("input_tokens"))
                outputs = usage.get("completion_tokens", usage.get("output_tokens"))
                if any(type(n) is not int or n < 0 for n in (inputs, outputs)):
                    raise EffectUncertainError("provider token usage is invalid")
                assert isinstance(inputs, int) and isinstance(outputs, int)
                if inputs > policy.max_input_tokens or outputs > policy.max_output_tokens:
                    raise MeasuredUsageError(
                        policy.cost(inputs, outputs),
                        CallUsage(input_tokens=inputs, output_tokens=outputs,
                                  http_status=response.status_code),
                    )
                return {
                    "body": base64.b64encode(raw).decode(),
                    "status": response.status_code,
                    "cost": policy.cost(inputs, outputs),
                    "usage": CallUsage(input_tokens=inputs, output_tokens=outputs,
                                       http_status=response.status_code).model_dump(),
                }
            finally:
                await response.aclose()

        value = await self.journal.run(
            task_id=self.task_id,
            key=key,
            request=identity,
            operation=dispatch,
            max_cny=policy.max_cny,
            max_calls=policy.max_calls,
            reserve_cny=policy.cost(policy.max_input_tokens, policy.max_output_tokens),
            reserve_calls=1,
            measure=lambda r: float(r["cost"]),
            cancel_event=self.cancel_event,
            max_attempts=1,
            context=request_context,
            usage=lambda r: CallUsage.model_validate(r["usage"]),
        )
        return httpx.Response(
            int(value["status"]),
            content=base64.b64decode(value["body"]),
            headers={"content-type": "application/json"},
        )


class MeteredLocalTransport(httpx.AsyncBaseTransport):
    """One explicitly identified Ollama operation; no hidden retries or warm-up."""

    def __init__(
        self, *, inner: httpx.AsyncBaseTransport, journal: EffectJournal, task_id: str,
        context: ContextSource, model_version: str, cancel_event: threading.Event,
    ) -> None:
        resolved_context = _resolve_context(context)
        if resolved_context.call_kind != "local_model":
            raise AuthorizationError("local transport requires local model context")
        self.inner = inner
        self.journal = journal
        self.task_id = task_id
        self.context = context
        self.model_version = model_version
        self.cancel_event = cancel_event

    async def aclose(self) -> None:
        await self.inner.aclose()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        context = _resolve_context(self.context)
        snapshot = self.journal.approved_policy(self.task_id)
        local = snapshot.local
        if (snapshot.digest() != context.policy_sha256 or local is None
                or local.model_version != self.model_version):
            raise AuthorizationError("local model identity differs from approved policy")
        if request.method != "POST" or request.url != httpx.URL(local.endpoint):
            raise AuthorizationError("request is outside the approved local inference route")
        content = await request.aread()
        request_context = context
        if len(content) > local.max_input_tokens:
            raise AuthorizationError("local request exceeds conservative input allowance")
        body = json.loads(content)
        if (not isinstance(body, dict) or body.get("model") != local.model
                or body.get("stream") is not False):
            raise AuthorizationError("local model and non-streaming mode must be explicit")
        options = body.get("options")
        limit = options.get("num_predict") if isinstance(options, dict) else None
        if type(limit) is not int or not 1 <= limit <= local.max_output_tokens:
            raise AuthorizationError("local output must have a finite approved limit")
        maximum, calls = self.journal.approved_limits(self.task_id)

        async def dispatch() -> dict[str, Any]:
            response = await self.inner.handle_async_request(request)
            try:
                raw = bytearray()
                async for block in response.aiter_bytes():
                    raw.extend(block)
                    if len(raw) > 2_000_000:
                        raise EffectUncertainError("local response exceeds its size limit")
                if not 200 <= response.status_code < 300:
                    raise EffectUncertainError("local response did not confirm successful usage")
                envelope = json.loads(raw)
                if (not isinstance(envelope, dict) or envelope.get("model") != local.model
                        or envelope.get("done") is not True):
                    raise EffectUncertainError("local response identity or completion is unknown")
                inputs, outputs = envelope.get("prompt_eval_count"), envelope.get("eval_count")
                if any(type(n) is not int or n < 0 for n in (inputs, outputs)):
                    raise EffectUncertainError("local token usage is unknown")
                assert isinstance(inputs, int) and isinstance(outputs, int)
                usage = CallUsage(input_tokens=inputs, output_tokens=outputs,
                                  duration_ns=envelope.get("total_duration"),
                                  http_status=response.status_code)
                if inputs > local.max_input_tokens or outputs > local.max_output_tokens:
                    raise MeasuredUsageError(0, usage)
                return {"body": base64.b64encode(raw).decode(), "status": response.status_code,
                        "usage": usage.model_dump()}
            finally:
                await response.aclose()

        value = await self.journal.run(
            task_id=self.task_id, key=request_context.effect_key(),
            request={"endpoint": local.endpoint, "body_sha256": hashlib.sha256(content).hexdigest(),
                     "policy_sha256": snapshot.digest()},
            operation=dispatch, max_cny=maximum, max_calls=calls,
            cancel_event=self.cancel_event, max_attempts=1, context=request_context,
            usage=lambda r: CallUsage.model_validate(r["usage"]),
        )
        return httpx.Response(int(value["status"]), content=base64.b64decode(value["body"]),
                              headers={"content-type": "application/json"})


class MeteredExternalTransport(httpx.AsyncBaseTransport):
    """Count a named retrieval/ingestion request independently of inference cost.

    Composition supplies the actual data-field scope. It remains responsible for
    paper ownership and approved search filters; arbitrary destinations and
    destructive cleanup are not admitted by this transport.
    """

    SEARCH_ENDPOINTS = {
        "arxiv": "https://export.arxiv.org/api/query",
        "openalex": "https://api.openalex.org/works",
        "crossref": "https://api.crossref.org/works",
        "semantic_scholar": "https://api.semanticscholar.org/graph/v1/paper/search",
    }

    def __init__(
        self, *, inner: httpx.AsyncBaseTransport, journal: EffectJournal, task_id: str,
        context: ContextSource, service: str, data_fields: frozenset[str],
        cancel_event: threading.Event, max_request_bytes: int = 32_000_000,
        max_response_bytes: int = 2_000_000,
    ) -> None:
        if _resolve_context(context).call_kind != "external_request":
            raise AuthorizationError("external transport requires external request context")
        for limit in (max_request_bytes, max_response_bytes):
            if type(limit) is not int or not 1 <= limit <= 64_000_000:
                raise ValueError("request and response limits must be bounded integers")
        if service not in {*self.SEARCH_ENDPOINTS, "arxiv_pdf", "documind"}:
            raise AuthorizationError("external service is unsupported")
        self.inner, self.journal, self.task_id = inner, journal, task_id
        self.context, self.service, self.data_fields = context, service, data_fields
        self.cancel_event = cancel_event
        self.max_request_bytes, self.max_response_bytes = max_request_bytes, max_response_bytes

    async def aclose(self) -> None:
        await self.inner.aclose()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        context = _resolve_context(self.context)
        policy = self.journal.approved_policy(self.task_id)
        if (policy.digest() != context.policy_sha256
                or not self.data_fields.issubset(policy.data_fields)):
            raise AuthorizationError("external request differs from approved data scope")
        if request.url.userinfo or request.url.fragment:
            raise AuthorizationError("external URL must not contain credentials or fragments")
        target = request.url.copy_with(query=None)
        if self.service in self.SEARCH_ENDPOINTS:
            if (self.service not in policy.allowed_search_providers or request.method != "GET"
                    or target != httpx.URL(self.SEARCH_ENDPOINTS[self.service])):
                raise AuthorizationError("search request is outside approved providers")
        elif self.service == "arxiv_pdf":
            if (
                request.method != "GET"
                or request.url.scheme != "https"
                or request.url.host not in {"arxiv.org", "export.arxiv.org"}
                or request.url.port not in {None, 443}
                or "arxiv" not in policy.allowed_search_providers
                or request.url.query
                or not re.fullmatch(r"/pdf/\d{4}\.\d{4,5}v\d+\.pdf", request.url.path)
            ):
                raise AuthorizationError("PDF request is outside the arXiv allowlist")
        else:
            if policy.documind_url is None:
                raise AuthorizationError("DocuMind is not configured in the approved policy")
            origin = httpx.URL(policy.documind_url)
            if (request.url.scheme, request.url.host, request.url.port) != (
                origin.scheme, origin.host, origin.port,
            ):
                raise AuthorizationError("DocuMind origin differs from approved policy")
            path = request.url.path
            allowed = (
                request.method == "GET" and (
                    path == "/api/v1/documents"
                    or re.fullmatch(r"/api/v1/documents/[A-Za-z0-9_-]{1,200}", path) is not None
                )
            ) or (request.method == "POST" and path in {
                "/api/v1/documents", "/api/v1/retrieve",
            })
            if not allowed or request.url.query:
                raise AuthorizationError("DocuMind operation is outside approved routes")
            if (path == "/api/v1/documents" and request.method == "POST"
                    and "selected_pdf" not in self.data_fields):
                raise AuthorizationError("ingestion requires selected PDF data approval")
        content = await request.aread()
        if len(content) > self.max_request_bytes:
            raise AuthorizationError("external request exceeds approved size allowance")
        request_context = context
        maximum, calls = self.journal.approved_limits(self.task_id)

        async def dispatch() -> dict[str, Any]:
            response = await self.inner.handle_async_request(request)
            try:
                raw = bytearray()
                async for block in response.aiter_bytes():
                    raw.extend(block)
                    if len(raw) > self.max_response_bytes:
                        raise EffectUncertainError("external response exceeds its size limit")
                if not 200 <= response.status_code < 300:
                    raise EffectUncertainError("external operation did not confirm success")
                return {"body": base64.b64encode(raw).decode(), "status": response.status_code,
                        "headers": {"content-type": response.headers.get("content-type", "")},
                        "usage": CallUsage(http_status=response.status_code).model_dump()}
            finally:
                await response.aclose()

        value = await self.journal.run(
            task_id=self.task_id, key=request_context.effect_key(),
            request={"method": request.method,
                     "url_sha256": hashlib.sha256(str(request.url).encode()).hexdigest(),
                     "body_sha256": hashlib.sha256(content).hexdigest(),
                     "policy_sha256": policy.digest(), "data_fields": sorted(self.data_fields)},
            operation=dispatch, max_cny=maximum, max_calls=calls,
            cancel_event=self.cancel_event, max_attempts=1, context=request_context,
            usage=lambda r: CallUsage.model_validate(r["usage"]),
        )
        return httpx.Response(int(value["status"]), content=base64.b64decode(value["body"]),
                              headers=value.get("headers", {}))


