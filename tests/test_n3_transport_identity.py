"""Stable operation identities reject request drift; PDF replay retains media type."""
import asyncio
import threading

import httpx
import pytest
from test_task_authorization import NOW, context, grant, phase, policy

from scholartrace.delivery.authorization import AuthorizationError
from scholartrace.delivery.effects import EffectConflictError, EffectJournal
from scholartrace.delivery.metering import MeteredExternalTransport


@pytest.mark.parametrize("scenario", ["replay", "drift", "port", "provider"])
def test_pdf_scope_identity_and_media_type(tmp_path, scenario):
    approved = policy(allowed_search_providers=() if scenario == "provider" else ("arxiv",))
    journal = EffectJournal(tmp_path / "effects.sqlite")
    journal.authorize_task(grant(policy=approved, deadline_at="2099-01-01T00:00:00+00:00"), now=NOW)
    execution = phase(policy_sha256=approved.digest(), authorization_id="execution:1",
                      phase="execution", plan_version=1, plan_digest="e" * 64,
                      max_external_requests=3)
    journal.prepare_phase(execution, now=NOW)
    journal.activate_phase(execution, now=NOW)
    ctx = context(call_kind="external_request", policy_sha256=approved.digest(),
                  authorization_id="execution:1", plan_version=1, plan_digest="e" * 64)
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=b"%PDF-synthetic", headers={
            "content-type": "application/pdf", "set-cookie": "private=discard",
        })

    async def run():
        transport = MeteredExternalTransport(
            inner=httpx.MockTransport(respond), journal=journal, task_id="task:synthetic",
            context=ctx, service="arxiv_pdf", data_fields=frozenset(),
            cancel_event=threading.Event(),
        )
        async with httpx.AsyncClient(transport=transport) as client:
            url = "https://arxiv.org/pdf/2401.12345v1.pdf"
            if scenario in {"port", "provider"}:
                if scenario == "port":
                    url = url.replace("arxiv.org", "arxiv.org:444")
                with pytest.raises(AuthorizationError):
                    await client.get(url)
                assert calls == []
                return
            first = await client.get(url)
            if scenario == "drift":
                with pytest.raises(EffectConflictError):
                    await client.get(url.replace("v1", "v2"))
            else:
                second = await client.get(url)
                assert second.content == first.content
                assert second.headers["content-type"] == "application/pdf"
                assert "set-cookie" not in second.headers
            assert len(calls) == 1
    try:
        asyncio.run(run())
    finally:
        journal.close()
