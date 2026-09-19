"""Explicit startup configuration is not task-level permission to dispatch."""

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from test_task_authorization import policy

from scholartrace.api.production import ProductionConfigurationError, create_production_app
from scholartrace.contracts import BudgetLimits


def write_config(tmp_path):
    remote = policy().remote
    local = remote.model_copy(update={
        "endpoint": "http://127.0.0.1:11434/api/chat", "protocol": "ollama",
        "input_cny_per_million": "0", "output_cny_per_million": "0",
    })
    config = {
        "policy": policy(local=local, documind_url="http://127.0.0.1:8000").model_dump(
            mode="json"
        ),
        "data_dir": "owned-data", "year_from": 2020, "retrieval_cutoff": "2026-09-18",
        "plan_limits": BudgetLimits(max_fulltext_papers=3).model_dump(mode="json"),
    }
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def test_factory_import_does_not_create_default_application(tmp_path):
    env = os.environ.copy()
    data = tmp_path / "must-not-exist"
    env.update(SCHOLARTRACE_DATA_DIR=str(data), PYTHONPATH="src")
    result = subprocess.run([
        sys.executable, "-B", "-c",
        "import scholartrace.api.production; import sys; "
        "assert 'scholartrace.api.app' not in sys.modules",
    ], env=env, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not data.exists()


def test_explicit_app_never_dispatches_without_gate_a(tmp_path, monkeypatch):
    calls = []

    async def forbidden(*args, **kwargs):
        calls.append("network")
        raise AssertionError("no provider request is permitted during startup or bare gates")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    path = write_config(tmp_path)
    app = create_production_app({
        "SCHOLARTRACE_RUNTIME_CONFIG": str(path),
        "SCHOLARTRACE_MODEL_API_KEY": "synthetic-key",
    })
    with TestClient(app) as client:
        created = client.post('/api/v1/research/tasks', json={
            "question": "A synthetic startup check", "execution_mode": "real",
        })
        assert created.status_code == 201
        task = created.json()['task_id']
        base = f'/api/v1/research/tasks/{task}'
        estimate = client.post(base + '/plan/estimate').json()
        assert estimate['production_composed'] is True
        assert estimate['configured'] is True
        assert 'synthetic-key' not in json.dumps(estimate)
        denied = client.post(base + '/plan/acknowledge-cost', json={
            "acknowledged_max_cny": 1,
        })
        assert denied.status_code == 409
        assert client.post(base + '/plan/generate').status_code == 409
        assert not calls
    assert (tmp_path / 'owned-data').is_dir()
    assert app.state.m6_service._closed


@pytest.mark.parametrize('environ', [{}, {'SCHOLARTRACE_RUNTIME_CONFIG': 'missing.json'}])
def test_missing_explicit_configuration_refuses(environ):
    with pytest.raises(ProductionConfigurationError):
        create_production_app(environ)


def test_invalid_configuration_error_does_not_echo_input(tmp_path):
    path = write_config(tmp_path)
    data = json.loads(path.read_text(encoding='utf-8'))
    data['accidental_secret'] = 'do-not-echo-this-value'
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ProductionConfigurationError) as exc:
        create_production_app({
            'SCHOLARTRACE_RUNTIME_CONFIG': str(path),
            'SCHOLARTRACE_MODEL_API_KEY': 'synthetic-key',
        })
    assert 'do-not-echo-this-value' not in str(exc.value)
    assert not (tmp_path / 'owned-data').exists()
