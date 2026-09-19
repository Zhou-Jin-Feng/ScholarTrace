"""Stage-bound HTTP dispatch for academic sources and DocuMind."""

from __future__ import annotations

import hashlib
import threading
from collections import defaultdict

import httpx

from scholartrace.delivery.authorization import AuthorizationError, CallContext
from scholartrace.delivery.effects import EffectJournal
from scholartrace.delivery.metering import MeteredExternalTransport
from scholartrace.delivery.stages import ACTIVE_RESEARCH_STAGE


class ResearchHttpTransport(httpx.AsyncBaseTransport):
    """Number requests within each deterministic stage and endpoint.

    A fresh execution session replays the same call order. Completed stages are
    skipped independently, so their request counters cannot shift another stage.
    Request bodies remain journal consistency checks, never new operation IDs.
    """

    def __init__(
        self,
        *,
        inner: httpx.AsyncBaseTransport,
        journal: EffectJournal,
        task_id: str,
        plan_version: int,
        plan_digest: str,
        service: str,
        cancel_event: threading.Event,
    ) -> None:
        self.inner, self.journal, self.task_id = inner, journal, task_id
        self.plan_version, self.plan_digest = plan_version, plan_digest
        self.service, self.cancel_event = service, cancel_event
        self._counts: dict[tuple[str, str, str], int] = defaultdict(int)

    async def aclose(self) -> None:
        await self.inner.aclose()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        stage = ACTIVE_RESEARCH_STAGE.get()
        if stage is None:
            raise AuthorizationError("research HTTP requires an active journaled stage")
        phase = self.journal.active_phase(self.task_id, "execution")
        if phase.plan_version != self.plan_version or phase.plan_digest != self.plan_digest:
            raise AuthorizationError("research HTTP differs from the approved execution plan")
        route = str(request.url.copy_with(query=None))
        identity = (stage, request.method, route)
        ordinal = self._counts[identity]
        self._counts[identity] += 1
        digest = hashlib.sha256(("\0".join(identity) + f"\0{ordinal}").encode()).hexdigest()
        context = CallContext(
            authorization_id=phase.authorization_id,
            operation_id="http:" + digest,
            call_kind="external_request",
            policy_sha256=phase.policy_sha256,
            plan_version=self.plan_version,
            plan_digest=self.plan_digest,
        )
        fields = frozenset({"question"})
        if self.service == "arxiv_pdf" or request.method == "GET" and self.service == "documind":
            fields = frozenset()
        elif self.service == "documind" and request.url.path == "/api/v1/documents":
            fields = frozenset({"selected_pdf"})
        transport = MeteredExternalTransport(
            inner=self.inner,
            journal=self.journal,
            task_id=self.task_id,
            context=context,
            service=self.service,
            data_fields=fields,
            cancel_event=self.cancel_event,
            max_response_bytes=32_000_000 if self.service == "arxiv_pdf" else 2_000_000,
        )
        return await transport.handle_async_request(request)
