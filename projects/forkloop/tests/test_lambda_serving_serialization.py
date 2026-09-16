"""Exercise real FastAPI request concurrency with a CPU-only fake generator."""
import base64
import concurrent.futures
import io
import sys
import threading
import time
from types import SimpleNamespace

import pytest

torch = pytest.importorskip('torch')
from PIL import Image
from fastapi.testclient import TestClient
from scripts import lambda_serve


def test_requests_are_serial_and_generation_failure_releases_lock(monkeypatch, tmp_path):
    state = {'active': 0, 'peak': 0, 'fail': False}
    state_lock = threading.Lock()

    class Model:
        config = SimpleNamespace(use_cache=False)
        def eval(self):
            return self
        def generate(self, **kwargs):
            with state_lock:
                state['active'] += 1
                state['peak'] = max(state['peak'], state['active'])
            try:
                time.sleep(0.08)
                if state['fail']:
                    raise RuntimeError('controlled fake generation failure')
                return torch.tensor([[1, 2, 3]])
            finally:
                with state_lock:
                    state['active'] -= 1

    class Inputs(dict):
        def to(self, device):
            # This integration test never allocates GPU memory.
            return self

    class Processor:
        tokenizer = SimpleNamespace(decode=lambda *a, **k: 'ok')
        def apply_chat_template(self, *a, **k):
            return 'fixed test prompt'
        def __call__(self, **kwargs):
            return Inputs(input_ids=torch.tensor([[1, 2]]), image_grid_thw=torch.tensor([[1, 1, 1]]))

    captured = {}
    monkeypatch.setattr(lambda_serve, 'load_model_and_processor', lambda *a, **k: (Model(), Processor()))
    monkeypatch.setattr('uvicorn.run', lambda app, **kwargs: captured.update(app=app))
    monkeypatch.setattr(sys, 'argv', ['lambda_serve', '--model', 'fake', '--log', str(tmp_path / 'requests.jsonl')])
    lambda_serve.main()
    buf = io.BytesIO()
    Image.new('RGB', (1, 1)).save(buf, format='PNG')
    body = {'messages': [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()}}]}], 'max_tokens': 4}
    with TestClient(captured['app']) as client:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: client.post('/v1/chat/completions', json=body), range(2)))
        assert [r.status_code for r in responses] == [200, 200]
        assert [r.json()['choices'][0]['message']['content'] for r in responses] == ['ok', 'ok']
        assert state['peak'] == 1
        state['fail'] = True
        with pytest.raises(RuntimeError, match='controlled fake'):
            client.post('/v1/chat/completions', json=body)
        state['fail'] = False
        assert client.post('/v1/chat/completions', json=body).status_code == 200
        assert state['active'] == 0
