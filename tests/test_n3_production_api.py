"""Both task gates and production adapters, with loopback synthetic dependencies."""

import json
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from production_api_peer import production_api_peer
from production_http_peer import production_http_peer
from production_synthetic import research_responder
from test_task_authorization import policy

from scholartrace.api.factory import create_app
from scholartrace.contracts import BudgetLimits
from scholartrace.delivery.production import ProductionResearchRunner


@pytest.mark.parametrize('verification_status,pdf_failure', [
    ('supported', False), ('supported', True),
    ('partially_supported', False), ('unsupported', False),
    ('mixed', False),
    ('conflicted', False),
    ('projection_recovery', False),
])
@pytest.mark.parametrize('api_wire', ['asgi', 'socket'])
def test_api_gates_to_production_result(
    tmp_path, monkeypatch, pdf_failure, api_wire, verification_status,
):
    from scholartrace.delivery import service as service_module

    original_pdf = service_module.render_pdf
    if pdf_failure:
        def fail_pdf(**kwargs):
            raise OSError('synthetic PDF render failure')

        monkeypatch.setattr(service_module, 'render_pdf', fail_pdf)
    requests = []
    synthesis_inputs = []
    responder_status = (
        'supported' if verification_status == 'projection_recovery' else verification_status
    )
    research = research_responder(requests, verification_status=responder_status,
                                  synthesis_inputs=synthesis_inputs)
    planned = False

    def respond(request):
        nonlocal planned
        if request.url.path == '/v1/chat/completions' and not planned:
            planned = True
            requests.append((request.method, request.url.path))
            draft = {
                'title': 'Retrieval research', 'objective': 'Compare retrieval evidence',
                'inclusion_criteria': ['Full text'], 'exclusion_criteria': ['Opinion only'],
                'sources': ['arxiv'], 'subquestions': [{
                    'question': 'What signals determine retrieval?',
                    'evidence_required': 'fulltext', 'priority': 'high',
                }],
            }
            return httpx.Response(200, json={
                'model': 'synthetic', 'usage': {'prompt_tokens': 100, 'completion_tokens': 20},
                'choices': [{'message': {'content': json.dumps(draft)}}],
            })
        return research(request)

    remote = policy().remote.model_copy(update={
        'max_input_tokens': 50000, 'max_output_tokens': 4096,
    })
    local = remote.model_copy(update={
        'endpoint': 'http://127.0.0.1:11434/api/chat', 'protocol': 'ollama',
        'model': 'local-synthetic', 'input_cny_per_million': '0', 'output_cny_per_million': '0',
    })
    approved = policy(remote=remote, local=local, documind_url='http://documind.test',
                      data_fields=('question', 'selected_pdf', 'paper_metadata',
                                   'retrieved_chunks', 'claims', 'evidence_quotes',
                                   'verification_results'))
    with production_http_peer(respond) as transport:
        runner = ProductionResearchRunner(
            policy=approved, api_key='x', data_dir=tmp_path / 'research',
            year_from=2020, retrieval_cutoff=date(2026, 9, 18),
            plan_limits=BudgetLimits(max_fulltext_papers=3, max_api_calls=10,
                                     max_cost_cny=1, max_duration_seconds=120),
            transport_factory=transport,
        )
        app = create_app(root=Path(__file__).resolve().parents[1], data_dir=tmp_path / 'service',
                         live_runner=runner, deployment_mode='loopback', auth_token='')
        with (TestClient(app) if api_wire == 'asgi' else production_api_peer(app)) as client:
            task = client.post('/api/v1/research/tasks', json={
                'question': 'What determines retrieval?', 'execution_mode': 'real',
            }).json()['task_id']
            base = f'/api/v1/research/tasks/{task}'
            assert client.post(base + '/plan/generate').status_code == 409
            assert not requests
            grant = {
                'policy_sha256': approved.digest(), 'max_cny': '1', 'max_remote_calls': 10,
                'max_local_calls': 10, 'max_external_requests': 30, 'generation': 1,
                'deadline_at': (datetime.now(UTC) + timedelta(seconds=290)).isoformat(),
                'max_wall_clock_seconds': 300,
                'planning': {'max_cny': '0.2', 'max_remote_calls': 1,
                             'max_local_calls': 0, 'max_external_requests': 0},
            }
            ack = client.post(base + '/plan/acknowledge-cost', json={
                'acknowledged_max_cny': 0.2, 'authorization': grant,
            })
            assert ack.status_code == 200, ack.text
            restored = client.post(base + '/plan/estimate').json()
            assert restored['has_task_authorization'] is True
            assert restored['planning_authorization'] == grant
            assert len(requests) == 0
            if verification_status == 'projection_recovery':
                def fail_projection(**kwargs):
                    raise OSError('synthetic projection storage failure')

                with monkeypatch.context() as patch:
                    patch.setattr(app.state.m6_service.ledger, 'project_confirmed_usage',
                                  fail_projection)
                    generated = client.post(base + '/plan/generate')
                    pending = client.get(base + '/budget').json()
                    assert pending['projection_state'] == 'pending'
                    assert pending['measured_usage'] is None
                    assert pending['journal_accounting']['committed_calls'] == 1
                    assert pending['journal_accounting']['known_cny'] > 0
                before = list(requests)
                recovered = client.get(base + '/budget').json()
                assert recovered['projection_state'] == 'synced'
                assert recovered['measured_usage']['api_calls'] == 1
                assert recovered['journal_accounting'] == pending['journal_accounting']
                assert client.get(base + '/budget').json() == recovered
                assert requests == before
            else:
                generated = client.post(base + '/plan/generate')
            assert generated.status_code == 200, generated.text
            plan = generated.json()
            assert len(requests) == 1
            bare = {'action': 'approve', 'plan_version': plan['plan_version'],
                    'plan_digest': plan['plan_digest']}
            assert client.post(base + '/approve', json=bare).status_code == 409
            assert len(requests) == 1
            accepted = client.post(base + '/approve', json={**bare, 'execution_authorization': {
                'policy_sha256': approved.digest(), 'generation': 1, 'max_cny': '0.8',
                'max_remote_calls': 9, 'max_local_calls': 10, 'max_external_requests': 30,
            }})
            assert accepted.status_code == 200, accepted.text
            deadline = time.monotonic() + 20
            while True:
                result = client.get(base).json()
                if result['status'] not in {'queued', 'running'} or time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
            expected_statuses = (
                {'failed', 'degraded'} if verification_status == 'unsupported'
                else {'completed', 'degraded'}
            )
            if verification_status == 'conflicted':
                # The synthetic analyzer supplies no counter-evidence. A remote
                # conflict label alone must not manufacture verified provenance.
                assert result['status'] in {'failed', 'interrupted'}, result
                assert synthesis_inputs == []
                assert client.get(base + '/claims').status_code != 200
                assert requests.count(('POST', '/v1/chat/completions')) == 2
                return
            assert result['status'] in expected_statuses, result
            claims = client.get(base + '/claims')
            assert claims.status_code == 200 and len(claims.json()['claims']) == 3
            assert requests.count(('POST', '/api/v1/documents')) == 3
            expected_calls = 4 if verification_status == 'unsupported' else 5
            assert requests.count(('POST', '/v1/chat/completions')) == expected_calls
            budget = client.get(base + '/budget').json()
            assert budget['journal_accounting']['uncertain_effects'] == 0
            assert budget['journal_accounting']['committed_calls'] == expected_calls
            assert budget['projection_state'] == 'synced'
            if pdf_failure:
                service = app.state.m6_service
                deadline = time.monotonic() + 10
                while not any(e['kind'] == 'task_terminal_no_exports'
                              for e in service.events(task)):
                    assert time.monotonic() < deadline, 'missing export failure terminal'
                    time.sleep(0.01)
                saved = service.research_result(task).model_dump_json()
                before = list(requests)
                assert client.get(base).json()['status'] == 'degraded'
                assert client.get(base + '/report?format=pdf').status_code == 404
                monkeypatch.setattr(service_module, 'render_pdf', original_pdf)
                # Exercise export recovery only; never re-approve research to repair a PDF.
                retried = client.post(base + '/report/retry')
                assert retried.status_code == 200, retried.text
                assert service.research_result(task).model_dump_json() == saved
                assert requests == before
                assert client.get(base + '/claims').json() == claims.json()
                assert client.get(base + '/budget').json() == budget
                assert client.get(base + '/report?format=pdf').content.startswith(b'%PDF')
                assert service.events(task)[-1]['kind'] == 'exports_ready'
            report = client.get(base + '/report?format=markdown')
            assert report.status_code == 200
            if verification_status == 'unsupported':
                assert synthesis_inputs == []
                assert 'No substantive answer is available.' in report.text
            elif verification_status == 'partially_supported':
                assert result['status'] == 'degraded'
                assert '[PARTIALLY SUPPORTED]' in report.text
                assert len(synthesis_inputs) == 1
            elif verification_status == 'mixed':
                persisted = app.state.m6_service.research_result(task)
                excluded = {v.claim_id for v in persisted.reliability.verifications
                            if v.status == 'unsupported'}
                assert len(excluded) == 1
                assert len(synthesis_inputs) == 1
                context = json.loads(synthesis_inputs[0]['evidence_context'])
                included = {c['claim_id'] for c in context['claims']}
                assert len(included) == 2 and not included & excluded
                assert {d['verification_status'] for d in context['dispositions']} == {
                    'supported', 'partially_supported',
                }
                assert len(synthesis_inputs[0]['allowed_evidence_ids']) == 2
                assert '[PARTIALLY SUPPORTED]' in report.text
