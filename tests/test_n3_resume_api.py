import asyncio
from pathlib import Path

import httpx
import pytest

from scholartrace.api.app import create_app
from scholartrace.delivery.authorization import AuthorizationError
from scholartrace.delivery.service import TaskQueueClosedError, TaskQueueFullError


@pytest.mark.parametrize('error,status,code', [
    (AuthorizationError('restored data requires reconciliation'), 409, 'authorization_refused'),
    (TaskQueueFullError('task queue is full'), 429, None),
    (TaskQueueClosedError('task queue is closed'), 503, None),
])
def test_resume_exposes_actionable_authorization_and_queue_status(tmp_path, monkeypatch,
                                                               error, status, code):
    app = create_app(root=Path(__file__).resolve().parents[1], data_dir=tmp_path)
    service = app.state.m6_service

    def refuse(*args, **kwargs):
        raise error

    monkeypatch.setattr(service, 'resume_task', refuse)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://127.0.0.1') as client:
            response = await client.post('/api/v1/research/tasks/task:synthetic/resume',
                                         json={'plan_version': 1, 'plan_digest': 'a' * 64})
            assert response.status_code == status
            if code:
                assert response.json()['detail']['code'] == code
            if status == 429:
                assert response.headers['retry-after'] == '1'
            assert service.queue_snapshot()['submitted'] == 0
    try:
        asyncio.run(run())
    finally:
        service.close()
