"""Isolated production browser host: every dependency uses synthetic loopback HTTP."""
import argparse
import json
import tempfile
from datetime import date
from pathlib import Path

import httpx
import uvicorn
from fastapi.staticfiles import StaticFiles
from production_http_peer import production_http_peer
from production_synthetic import research_responder
from test_task_authorization import policy

from scholartrace.api.factory import create_app
from scholartrace.contracts import BudgetLimits
from scholartrace.delivery.production import ProductionResearchRunner


def serve(tmp_path, port):
    requests = []
    research = research_responder(requests)
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

        @app.get('/synthetic-audit')
        def audit():
            return {'requests': requests}

        assets = Path(__file__).resolve().parents[1] / 'frontend/dist'
        app.mount('/', StaticFiles(directory=assets, html=True))
        uvicorn.run(app, host='127.0.0.1', port=port, access_log=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18765)
    args = parser.parse_args()
    base = Path(__file__).resolve().parents[1] / 'agent/browser-production-data'
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as data:
        serve(Path(data), args.port)
