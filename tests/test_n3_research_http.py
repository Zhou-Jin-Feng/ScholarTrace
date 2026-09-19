import asyncio
import threading

import httpx
import pytest
from test_task_authorization import NOW, grant, phase

from scholartrace.delivery.authorization import AuthorizationError
from scholartrace.delivery.effects import EffectJournal
from scholartrace.delivery.live_http import ResearchHttpTransport
from scholartrace.delivery.stages import ACTIVE_RESEARCH_STAGE


def test_stage_http_replays_without_counter_drift_from_skipped_stages(tmp_path):
    journal = EffectJournal(tmp_path / 'effects.sqlite')
    journal.authorize_task(grant(deadline_at='2099-01-01T00:00:00+00:00'), now=NOW)
    execution = phase(authorization_id='execution:1', phase='execution', plan_version=1,
                      plan_digest='e' * 64, max_external_requests=5)
    journal.prepare_phase(execution, now=NOW)
    journal.activate_phase(execution, now=NOW)
    calls = []

    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(200, content=b'%PDF-synthetic',
                              headers={'content-type': 'application/pdf'})

    async def run(restarted):
        transport = ResearchHttpTransport(
            inner=httpx.MockTransport(respond), journal=journal, task_id='task:synthetic',
            plan_version=1, plan_digest='e' * 64, service='arxiv_pdf',
            cancel_event=threading.Event(),
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(AuthorizationError, match='active journaled stage'):
                await client.get('https://arxiv.org/pdf/2401.12345v1.pdf')
            for stage in (['acquire:two'] if restarted else ['acquire:one', 'acquire:two']):
                token = ACTIVE_RESEARCH_STAGE.set(stage)
                try:
                    r = await client.get('https://arxiv.org/pdf/2401.12345v1.pdf')
                    assert r.headers['content-type'] == 'application/pdf'
                finally:
                    ACTIVE_RESEARCH_STAGE.reset(token)
    try:
        asyncio.run(run(False))
        asyncio.run(run(True))
        assert len(calls) == 2
    finally:
        journal.close()


def test_stage_http_accepts_canonical_arxiv_pdf_route(tmp_path):
    journal = EffectJournal(tmp_path / 'effects.sqlite')
    journal.authorize_task(grant(deadline_at='2099-01-01T00:00:00+00:00'), now=NOW)
    execution = phase(authorization_id='execution:1', phase='execution', plan_version=1,
                      plan_digest='c' * 64, max_external_requests=5)
    journal.prepare_phase(execution, now=NOW)
    journal.activate_phase(execution, now=NOW)

    async def run():
        transport = ResearchHttpTransport(
            inner=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, content=b'%PDF-synthetic',
                    headers={'content-type': 'application/pdf'},
                )
            ),
            journal=journal, task_id='task:synthetic', plan_version=1,
            plan_digest='c' * 64, service='arxiv_pdf',
            cancel_event=threading.Event(),
        )
        async with httpx.AsyncClient(transport=transport) as client:
            token = ACTIVE_RESEARCH_STAGE.set('acquire:one')
            try:
                response = await client.get('https://arxiv.org/pdf/2401.12345v1')
                assert response.status_code == 200
                unversioned = await client.get('https://arxiv.org/pdf/2401.12345')
                assert unversioned.status_code == 200
            finally:
                ACTIVE_RESEARCH_STAGE.reset(token)

    try:
        asyncio.run(run())
    finally:
        journal.close()
